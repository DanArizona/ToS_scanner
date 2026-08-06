from __future__ import annotations

import threading
from typing import Any

import pytest

from scan_command_loop import (
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
            "shutdown_requested": True,
            "loop_state": "stopped",
            "current_job": None,
            "last_result": None,
            "force": True,
        }
    ]
