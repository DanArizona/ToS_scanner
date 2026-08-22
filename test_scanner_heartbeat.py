from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from scan_jobs import (
    JobKind,
    JobOrigin,
    JobRequest,
    JobResult,
)
from scanner_heartbeat import ScannerHeartbeatPublisher


def make_request() -> JobRequest:
    return JobRequest(
        kind=JobKind.EXPORT_WL,
        origin=JobOrigin.FILE_COMMAND,
        requested_at=datetime(2026, 7, 26, 2, 0, 0),
        command_id="heartbeat-test-command",
    )


def test_publish_creates_heartbeat_file(
    tmp_path: Path,
) -> None:
    publisher = ScannerHeartbeatPublisher(
        command_root=tmp_path,
        interval_s=5.0,
    )

    written = publisher.publish(
        running=True,
        paused=False,
        shutdown_requested=False,
        loop_state="idle",
        force=True,
    )

    assert written is True

    heartbeat_path = (
        tmp_path / "status" / "scanner_heartbeat.json"
    )
    assert heartbeat_path.is_file()

    payload = json.loads(
        heartbeat_path.read_text(encoding="utf-8")
    )

    assert payload["schema_version"] == 1
    assert payload["application"] == "ToS_scanner"
    assert payload["heartbeat_sequence"] == 1
    assert payload["loop_state"] == "idle"
    assert (
        payload["exports_suspended"]
        is False
    )
    assert (
        payload["exports_suspended_since_utc"]
        is None
    )
    assert (
        payload["suspension_age_seconds"]
        is None
    )
    assert (
        payload["suspension_command_id"]
        is None
    )
    assert (
        payload["state_health"]
        == "NORMAL"
    )
    assert payload["running"] is True
    assert payload["paused"] is False
    assert payload["shutdown_requested"] is False
    assert payload["current_job"] is None
    assert payload["last_job"] is None
    assert isinstance(payload["pid"], int)
    assert payload["heartbeat_at_utc"].endswith("Z")


def test_publish_includes_export_suspension_metadata(
    tmp_path: Path,
) -> None:
    publisher = ScannerHeartbeatPublisher(
        command_root=tmp_path,
    )

    publisher.publish(
        running=True,
        paused=False,
        shutdown_requested=False,
        loop_state="exports_suspended",
        exports_suspended=True,
        exports_suspended_since_utc=(
            "2026-08-21T15:01:26Z"
        ),
        suspension_age_seconds=187.5,
        suspension_command_id=(
            "mb-suspend_exports-test"
        ),
        state_health="DEGRADED",
        force=True,
    )

    payload = json.loads(
        publisher.heartbeat_path.read_text(
            encoding="utf-8"
        )
    )

    assert (
        payload["exports_suspended"]
        is True
    )
    assert (
        payload["exports_suspended_since_utc"]
        == "2026-08-21T15:01:26Z"
    )
    assert (
        payload["suspension_age_seconds"]
        == 187.5
    )
    assert (
        payload["suspension_command_id"]
        == "mb-suspend_exports-test"
    )
    assert (
        payload["state_health"]
        == "DEGRADED"
    )


def test_publish_includes_current_job_and_last_result(
    tmp_path: Path,
) -> None:
    publisher = ScannerHeartbeatPublisher(
        command_root=tmp_path,
    )

    request = make_request()
    result = JobResult(
        request=request,
        ok=True,
        message="Export completed.",
    )

    publisher.publish(
        running=True,
        paused=False,
        shutdown_requested=False,
        loop_state="busy",
        current_job=request,
        last_result=result,
        force=True,
    )

    payload = json.loads(
        publisher.heartbeat_path.read_text(encoding="utf-8")
    )

    assert payload["current_job"] == {
        "kind": "export_wl",
        "command_id": "heartbeat-test-command",
        "origin": "file_command",
        "requested_at": "2026-07-26T02:00:00",
    }

    assert payload["last_job"] == {
        "kind": "export_wl",
        "command_id": "heartbeat-test-command",
        "ok": True,
        "message": "Export completed.",
        "error": None,
    }


def test_publish_is_rate_limited_without_force(
    tmp_path: Path,
) -> None:
    publisher = ScannerHeartbeatPublisher(
        command_root=tmp_path,
        interval_s=60.0,
    )

    first_written = publisher.publish(
        running=False,
        paused=False,
        shutdown_requested=False,
        loop_state="idle",
    )

    second_written = publisher.publish(
        running=True,
        paused=False,
        shutdown_requested=False,
        loop_state="idle",
    )

    assert first_written is True
    assert second_written is False

    payload = json.loads(
        publisher.heartbeat_path.read_text(encoding="utf-8")
    )

    assert payload["heartbeat_sequence"] == 1
    assert payload["running"] is False


def test_force_bypasses_rate_limit(
    tmp_path: Path,
) -> None:
    publisher = ScannerHeartbeatPublisher(
        command_root=tmp_path,
        interval_s=60.0,
    )

    publisher.publish(
        running=False,
        paused=False,
        shutdown_requested=False,
        loop_state="idle",
    )

    second_written = publisher.publish(
        running=True,
        paused=True,
        shutdown_requested=False,
        loop_state="paused",
        force=True,
    )

    assert second_written is True

    payload = json.loads(
        publisher.heartbeat_path.read_text(encoding="utf-8")
    )

    assert payload["heartbeat_sequence"] == 2
    assert payload["running"] is True
    assert payload["paused"] is True
    assert payload["loop_state"] == "paused"
