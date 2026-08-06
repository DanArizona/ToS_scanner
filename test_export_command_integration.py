from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pytest

from export_gate import ExportGate
from scan_command_loop import (
    _load_runtime_flags,
)
from scan_dispatcher import (
    ScanDispatcher,
    ScanRuntimeFlags,
)
from scan_jobs import (
    JobKind,
    JobOrigin,
    JobRequest,
    JobResult,
)
from scanner_heartbeat import (
    ScannerHeartbeatPublisher,
)


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def replace_wl_symbols(
        self,
        request: JobRequest,
    ) -> JobResult:
        self.calls.append(
            "replace_wl_symbols"
        )
        return JobResult(
            request=request,
            ok=True,
            message="replacement executed",
        )

    def add_wl_symbols(
        self,
        request: JobRequest,
    ) -> JobResult:
        self.calls.append(
            "add_wl_symbols"
        )
        return JobResult(
            request=request,
            ok=True,
            message="addition executed",
        )

    def export_wl(
        self,
        request: JobRequest,
    ) -> JobResult:
        self.calls.append("export_wl")
        return JobResult(
            request=request,
            ok=True,
            message="WL export executed",
        )

    def export_ts(
        self,
        request: JobRequest,
    ) -> JobResult:
        self.calls.append("export_ts")
        return JobResult(
            request=request,
            ok=True,
            message="TS export executed",
        )

    def export_tm(
        self,
        request: JobRequest,
    ) -> JobResult:
        self.calls.append("export_tm")
        return JobResult(
            request=request,
            ok=True,
            message="TM export executed",
        )


def make_request(
    kind: JobKind,
) -> JobRequest:
    return JobRequest(
        kind=kind,
        origin=JobOrigin.FILE_COMMAND,
        requested_at=datetime(
            2026,
            8,
            6,
            15,
            0,
        ),
        symbols=["AAA"],
        command_id=f"test-{kind.value}",
    )


def test_new_job_kind_values() -> None:
    assert (
        JobKind.SUSPEND_EXPORTS.value
        == "suspend_exports"
    )
    assert (
        JobKind.RESUME_EXPORTS.value
        == "resume_exports"
    )


def test_suspension_blocks_exports_but_allows_watchlist_update(
    tmp_path: Path,
) -> None:
    gate = ExportGate(tmp_path)
    flags = ScanRuntimeFlags(
        running=True
    )
    executor = RecordingExecutor()
    dispatcher = ScanDispatcher(
        flags=flags,
        action_executor=executor,
        export_gate=gate,
    )

    suspended = dispatcher.execute(
        make_request(
            JobKind.SUSPEND_EXPORTS
        )
    )

    assert suspended.ok is True
    assert flags.exports_suspended is True
    assert gate.is_suspended() is True

    replacement = dispatcher.execute(
        make_request(
            JobKind.REPLACE_WL_SYMBOLS
        )
    )
    blocked_export = dispatcher.execute(
        make_request(
            JobKind.EXPORT_WL
        )
    )

    assert replacement.message == (
        "replacement executed"
    )
    assert blocked_export.message == (
        "Skipped because scheduled exports "
        "are suspended."
    )
    assert executor.calls == [
        "replace_wl_symbols"
    ]

    resumed = dispatcher.execute(
        make_request(
            JobKind.RESUME_EXPORTS
        )
    )
    completed_export = (
        dispatcher.execute(
            make_request(
                JobKind.EXPORT_WL
            )
        )
    )

    assert resumed.ok is True
    assert flags.exports_suspended is False
    assert gate.is_suspended() is False
    assert completed_export.message == (
        "WL export executed"
    )
    assert executor.calls == [
        "replace_wl_symbols",
        "export_wl",
    ]


def test_hard_pause_remains_independent_from_export_suspension(
    tmp_path: Path,
) -> None:
    gate = ExportGate(tmp_path)
    flags = ScanRuntimeFlags(
        running=True
    )
    executor = RecordingExecutor()
    dispatcher = ScanDispatcher(
        flags=flags,
        action_executor=executor,
        export_gate=gate,
    )

    dispatcher.execute(
        make_request(
            JobKind.SUSPEND_EXPORTS
        )
    )
    dispatcher.execute(
        make_request(JobKind.PAUSE)
    )

    paused_replacement = (
        dispatcher.execute(
            make_request(
                JobKind.REPLACE_WL_SYMBOLS
            )
        )
    )

    assert paused_replacement.message == (
        "Skipped because scanner is paused."
    )
    assert executor.calls == []

    dispatcher.execute(
        make_request(JobKind.RESUME)
    )

    resumed_replacement = (
        dispatcher.execute(
            make_request(
                JobKind.REPLACE_WL_SYMBOLS
            )
        )
    )

    assert resumed_replacement.message == (
        "replacement executed"
    )
    assert flags.exports_suspended is True

    dispatcher.execute(
        make_request(
            JobKind.RESUME_EXPORTS
        )
    )


def test_suspension_failure_returns_failed_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = ExportGate(tmp_path)
    flags = ScanRuntimeFlags(
        running=True
    )
    dispatcher = ScanDispatcher(
        flags=flags,
        export_gate=gate,
    )

    def fail_suspend(**kwargs):
        raise TimeoutError(
            "simulated active export"
        )

    monkeypatch.setattr(
        gate,
        "suspend",
        fail_suspend,
    )

    result = dispatcher.execute(
        make_request(
            JobKind.SUSPEND_EXPORTS
        )
    )

    assert result.ok is False
    assert (
        "simulated active export"
        in result.message
    )
    assert flags.exports_suspended is False


def test_heartbeat_includes_export_suspension(
    tmp_path: Path,
) -> None:
    publisher = ScannerHeartbeatPublisher(
        command_root=tmp_path,
    )

    publisher.publish(
        running=True,
        paused=False,
        exports_suspended=True,
        shutdown_requested=False,
        loop_state="exports_suspended",
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
        payload["loop_state"]
        == "exports_suspended"
    )


def test_runtime_flags_load_persisted_suspension(
    tmp_path: Path,
) -> None:
    gate = ExportGate(tmp_path)
    gate.suspend(
        command_id="persisted-suspend"
    )
    gate.close()

    logger = logging.getLogger(
        "test_export_command_integration"
    )
    flags = _load_runtime_flags(
        gate,
        logger,
    )

    assert flags.exports_suspended is True

    gate.resume(
        command_id="test-cleanup"
    )


def test_start_and_normal_resume_do_not_clear_export_suspension(
    tmp_path: Path,
) -> None:
    gate = ExportGate(tmp_path)
    flags = ScanRuntimeFlags()
    dispatcher = ScanDispatcher(
        flags=flags,
        export_gate=gate,
    )

    dispatcher.execute(
        make_request(
            JobKind.SUSPEND_EXPORTS
        )
    )
    dispatcher.execute(
        make_request(JobKind.START)
    )
    dispatcher.execute(
        make_request(JobKind.RESUME)
    )

    assert flags.running is True
    assert flags.paused is False
    assert flags.exports_suspended is True
    assert gate.is_suspended() is True

    dispatcher.execute(
        make_request(
            JobKind.RESUME_EXPORTS
        )
    )
