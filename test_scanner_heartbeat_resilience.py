from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import scanner_heartbeat
from scanner_heartbeat import ScannerHeartbeatPublisher


def publish_idle(
    publisher: ScannerHeartbeatPublisher,
    *,
    force: bool = True,
) -> bool:
    return publisher.publish(
        running=True,
        paused=False,
        shutdown_requested=False,
        loop_state="idle",
        force=force,
    )


def test_transient_replace_errors_are_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_replace = Path.replace
    replace_attempts = 0
    sleep_delays: list[float] = []

    def flaky_replace(
        source: Path,
        target: Path,
    ) -> Path:
        nonlocal replace_attempts
        replace_attempts += 1

        if replace_attempts < 3:
            raise PermissionError(
                "simulated transient Windows lock"
            )

        return original_replace(source, target)

    monkeypatch.setattr(
        Path,
        "replace",
        flaky_replace,
    )
    monkeypatch.setattr(
        scanner_heartbeat.time,
        "sleep",
        sleep_delays.append,
    )

    publisher = ScannerHeartbeatPublisher(
        command_root=tmp_path,
        replace_retry_attempts=4,
        replace_retry_initial_delay_s=0.01,
        replace_retry_max_delay_s=0.10,
    )

    assert publish_idle(publisher) is True
    assert replace_attempts == 3
    assert sleep_delays == [0.01, 0.02]

    payload = json.loads(
        publisher.heartbeat_path.read_text(
            encoding="utf-8"
        )
    )
    assert payload["heartbeat_sequence"] == 1
    assert payload["loop_state"] == "idle"
    assert list(
        publisher.status_dir.glob(
            ".scanner_heartbeat.json.*.tmp"
        )
    ) == []


def test_exhausted_permission_errors_skip_publish_without_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    publisher = ScannerHeartbeatPublisher(
        command_root=tmp_path,
        replace_retry_attempts=3,
        replace_retry_initial_delay_s=0.01,
        replace_retry_max_delay_s=0.10,
    )

    assert publish_idle(publisher) is True

    initial_payload = json.loads(
        publisher.heartbeat_path.read_text(
            encoding="utf-8"
        )
    )

    replace_attempts = 0
    sleep_delays: list[float] = []

    def locked_replace(
        source: Path,
        target: Path,
    ) -> Path:
        nonlocal replace_attempts
        replace_attempts += 1
        raise PermissionError(
            "simulated persistent Windows lock"
        )

    monkeypatch.setattr(
        Path,
        "replace",
        locked_replace,
    )
    monkeypatch.setattr(
        scanner_heartbeat.time,
        "sleep",
        sleep_delays.append,
    )
    caplog.set_level(
        logging.ERROR,
        logger=scanner_heartbeat.LOGGER_NAME,
    )

    written = publisher.publish(
        running=False,
        paused=False,
        shutdown_requested=True,
        loop_state="stopped",
        force=True,
    )

    assert written is False
    assert replace_attempts == 3
    assert sleep_delays == [0.01, 0.02]
    assert (
        "scanner loop will continue"
        in caplog.text
    )

    # The prior valid heartbeat remains readable and unchanged.
    final_payload = json.loads(
        publisher.heartbeat_path.read_text(
            encoding="utf-8"
        )
    )
    assert final_payload == initial_payload

    # Failed attempt files are cleaned up.
    assert list(
        publisher.status_dir.glob(
            ".scanner_heartbeat.json.*.tmp"
        )
    ) == []


def test_non_permission_write_error_is_not_hidden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = ScannerHeartbeatPublisher(
        command_root=tmp_path,
    )

    def failed_replace(
        source: Path,
        target: Path,
    ) -> Path:
        raise OSError(
            "simulated non-permission filesystem failure"
        )

    monkeypatch.setattr(
        Path,
        "replace",
        failed_replace,
    )

    with pytest.raises(
        OSError,
        match="non-permission filesystem failure",
    ):
        publish_idle(publisher)

    assert list(
        publisher.status_dir.glob(
            ".scanner_heartbeat.json.*.tmp"
        )
    ) == []


@pytest.mark.parametrize(
    ("keyword", "value", "message"),
    [
        (
            "replace_retry_attempts",
            0,
            "must be at least one",
        ),
        (
            "replace_retry_initial_delay_s",
            -0.01,
            "cannot be negative",
        ),
        (
            "replace_retry_max_delay_s",
            -0.01,
            "cannot be negative",
        ),
    ],
)
def test_invalid_retry_configuration_is_rejected(
    tmp_path: Path,
    keyword: str,
    value: int | float,
    message: str,
) -> None:
    with pytest.raises(
        ValueError,
        match=message,
    ):
        ScannerHeartbeatPublisher(
            command_root=tmp_path,
            **{keyword: value},
        )


def test_retry_max_delay_cannot_be_less_than_initial_delay(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="cannot be less than",
    ):
        ScannerHeartbeatPublisher(
            command_root=tmp_path,
            replace_retry_initial_delay_s=0.10,
            replace_retry_max_delay_s=0.05,
        )
