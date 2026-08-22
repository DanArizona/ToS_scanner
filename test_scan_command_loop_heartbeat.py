from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import pytest

from export_gate import ExportGateSnapshot
from scan_command_loop import (
    _publish_heartbeat,
    _publish_stopped_heartbeat,
    _wait_for_operator,
)
from scan_dispatcher import ScanRuntimeFlags


class RecordingHeartbeat:
    def __init__(self) -> None:
        self.calls: list[
            dict[str, Any]
        ] = []
        self.refresh_seen = (
            threading.Event()
        )

    def publish(
        self,
        **kwargs: Any,
    ) -> bool:
        self.calls.append(kwargs)

        if len(self.calls) >= 2:
            self.refresh_seen.set()

        return True


def test_publish_heartbeat_derives_normal_suspension_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat = RecordingHeartbeat()

    flags = ScanRuntimeFlags(
        exports_suspended=True
    )

    snapshot = ExportGateSnapshot(
        suspended=True,
        generation=1,
        updated_at_utc=(
            "2026-08-21T15:00:00Z"
        ),
        command_id="suspend-normal-test",
    )

    class FakeExportGate:
        def snapshot(
            self,
        ) -> ExportGateSnapshot:
            return snapshot

    monkeypatch.setattr(
        "scan_command_loop._utc_now",
        lambda: datetime(
            2026,
            8,
            21,
            15,
            0,
            59,
            tzinfo=timezone.utc,
        ),
    )

    _publish_heartbeat(
        heartbeat=heartbeat,  # type: ignore[arg-type]
        export_gate=FakeExportGate(),  # type: ignore[arg-type]
        flags=flags,
        loop_state="exports_suspended",
    )

    call = heartbeat.calls[0]

    assert (
        call["suspension_age_seconds"]
        == 59.0
    )
    assert (
        call["state_health"]
        == "NORMAL"
    )


def test_publish_heartbeat_derives_degraded_suspension_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat = RecordingHeartbeat()

    flags = ScanRuntimeFlags(
        exports_suspended=True
    )

    snapshot = ExportGateSnapshot(
        suspended=True,
        generation=1,
        updated_at_utc=(
            "2026-08-21T15:00:00Z"
        ),
        command_id="suspend-degraded-test",
    )

    class FakeExportGate:
        def snapshot(
            self,
        ) -> ExportGateSnapshot:
            return snapshot

    monkeypatch.setattr(
        "scan_command_loop._utc_now",
        lambda: datetime(
            2026,
            8,
            21,
            15,
            2,
            0,
            tzinfo=timezone.utc,
        ),
    )

    _publish_heartbeat(
        heartbeat=heartbeat,  # type: ignore[arg-type]
        export_gate=FakeExportGate(),  # type: ignore[arg-type]
        flags=flags,
        loop_state="exports_suspended",
    )

    call = heartbeat.calls[0]

    assert (
        call["suspension_age_seconds"]
        == 120.0
    )
    assert (
        call["state_health"]
        == "DEGRADED"
    )


def test_publish_heartbeat_derives_warning_suspension_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat = RecordingHeartbeat()

    flags = ScanRuntimeFlags(
        exports_suspended=True
    )

    snapshot = ExportGateSnapshot(
        suspended=True,
        generation=1,
        updated_at_utc=(
            "2026-08-21T15:00:00Z"
        ),
        command_id="suspend-test",
    )

    class FakeExportGate:
        def snapshot(
            self,
        ) -> ExportGateSnapshot:
            return snapshot

    monkeypatch.setattr(
        "scan_command_loop._utc_now",
        lambda: datetime(
            2026,
            8,
            21,
            15,
            1,
            30,
            tzinfo=timezone.utc,
        ),
    )

    _publish_heartbeat(
        heartbeat=heartbeat,  # type: ignore[arg-type]
        export_gate=FakeExportGate(),  # type: ignore[arg-type]
        flags=flags,
        loop_state="exports_suspended",
    )

    assert len(heartbeat.calls) == 1

    call = heartbeat.calls[0]

    assert (
        call["exports_suspended"]
        is True
    )
    assert (
        call["exports_suspended_since_utc"]
        == "2026-08-21T15:00:00Z"
    )
    assert (
        call["suspension_age_seconds"]
        == 90.0
    )
    assert (
        call["suspension_command_id"]
        == "suspend-test"
    )
    assert (
        call["state_health"]
        == "WARNING"
    )


def test_wait_for_operator_refreshes_waiting_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat = RecordingHeartbeat()
    flags = ScanRuntimeFlags(
        exports_suspended=True
    )

    def fake_input(prompt: str) -> str:
        assert (
            "Press Enter when ready"
            in prompt
        )
        assert heartbeat.refresh_seen.wait(
            timeout=1.0
        )
        return ""

    monkeypatch.setattr(
        "builtins.input",
        fake_input,
    )

    _wait_for_operator(
        heartbeat=heartbeat,  # type: ignore[arg-type]
        flags=flags,
        last_result=None,
        refresh_poll_s=0.01,
    )

    assert len(heartbeat.calls) >= 2

    first_call = heartbeat.calls[0]
    refresh_call = heartbeat.calls[1]

    assert (
        first_call["loop_state"]
        == "waiting_for_operator"
    )
    assert first_call["force"] is True
    assert (
        first_call["exports_suspended"]
        is True
    )

    assert (
        refresh_call["loop_state"]
        == "waiting_for_operator"
    )
    assert (
        refresh_call.get(
            "force",
            False,
        )
        is False
    )
    assert (
        refresh_call[
            "exports_suspended"
        ]
        is True
    )


def test_publish_stopped_heartbeat_clears_runtime_flags() -> None:
    heartbeat = RecordingHeartbeat()
    flags = ScanRuntimeFlags()

    flags.running = True
    flags.paused = True
    flags.exports_suspended = True
    flags.shutdown_requested = False

    _publish_stopped_heartbeat(
        heartbeat=heartbeat,  # type: ignore[arg-type]
        flags=flags,
        last_result=None,
    )

    assert flags.running is False
    assert flags.paused is False
    assert flags.exports_suspended is True
    assert (
        flags.shutdown_requested is True
    )
    assert heartbeat.calls == [
        {
            "running": False,
            "paused": False,
            "exports_suspended": True,
            "exports_suspended_since_utc": None,
            "suspension_age_seconds": None,
            "suspension_command_id": None,
            "state_health": "NORMAL",
            "shutdown_requested": True,
            "loop_state": "stopped",
            "current_job": None,
            "last_result": None,
            "force": True,
        }
    ]
