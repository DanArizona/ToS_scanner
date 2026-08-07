from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import scan_runner
from export_gate import (
    DEFAULT_COMMAND_ROOT,
    ExportGate,
    resolve_command_root,
)
from scan_runner import ScanRunner


ET = ZoneInfo("America/New_York")
SLOT = datetime(
    2026,
    8,
    7,
    10,
    0,
    5,
    tzinfo=ET,
)


class RecordingState:
    def __init__(self) -> None:
        self.phases: list[str] = []
        self.pending: list[
            tuple[datetime, Path]
        ] = []
        self.completed: list[datetime] = []
        self.failures: list[str] = []

    def touch(self, *, phase: str) -> None:
        self.phases.append(phase)

    def set_pending(
        self,
        slot: datetime,
        path: Path,
    ) -> None:
        self.pending.append((slot, path))

    def clear_pending(
        self,
        *,
        phase: str,
    ) -> None:
        self.phases.append(phase)

    def mark_completed(
        self,
        slot: datetime,
    ) -> None:
        self.completed.append(slot)

    def mark_failure(
        self,
        message: str,
    ) -> None:
        self.failures.append(message)


class FakeFlags:
    def snapshot(self) -> tuple[bool, int]:
        return False, 0


class FakePauseController:
    def is_paused(self) -> bool:
        return False

    def snapshot(self) -> tuple[bool, int]:
        return False, 0


class RecordingLease:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class RecordingExporter:
    def __init__(
        self,
        stop_event: threading.Event,
        *,
        failure: Exception | None = None,
    ) -> None:
        self.stop_event = stop_event
        self.failure = failure
        self.calls: list[
            tuple[Path, datetime]
        ] = []

    def _record(
        self,
        path: Path,
        slot: datetime,
    ) -> None:
        self.calls.append((path, slot))
        self.stop_event.set()

        if self.failure is not None:
            raise self.failure

    def export_scan(
        self,
        path: Path,
        slot: datetime,
        *,
        stop_event: threading.Event,
    ) -> None:
        self._record(path, slot)

    def export_watchlist(
        self,
        path: Path,
        slot: datetime,
        *,
        stop_event: threading.Event,
    ) -> None:
        self._record(path, slot)


class FakeGate:
    def __init__(
        self,
        *,
        suspended: bool = False,
        wait_result: bool = True,
        lease: RecordingLease | None = None,
        stop_on_try: threading.Event | None = None,
    ) -> None:
        self.suspended = suspended
        self.wait_result = wait_result
        self.lease = lease
        self.stop_on_try = stop_on_try
        self.wait_calls = 0
        self.try_calls = 0

    def is_suspended(self) -> bool:
        return self.suspended

    def wait_until_resumed(
        self,
        stop_event: threading.Event,
    ) -> bool:
        self.wait_calls += 1
        return self.wait_result

    def try_begin_export(
        self,
    ) -> RecordingLease | None:
        self.try_calls += 1

        if self.stop_on_try is not None:
            self.stop_on_try.set()

        return self.lease


def make_runner(
    tmp_path: Path,
    *,
    gate: FakeGate,
    exporter: RecordingExporter,
    stop_event: threading.Event,
) -> tuple[ScanRunner, RecordingState]:
    state = RecordingState()
    runner = ScanRunner(
        exporter=exporter,
        logger=logging.getLogger(
            "test_scan_runner_export_gate"
        ),
        shared_state=state,
        stop_event=stop_event,
        output_dir=tmp_path / "output",
        flags=FakeFlags(),
        pause_ctl=FakePauseController(),
        export_gate=gate,
    )
    return runner, state


def patch_fired_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        scan_runner,
        "next_slot_after",
        lambda now, gate_active: SLOT,
    )
    monkeypatch.setattr(
        scan_runner,
        "wait_until_dynamic",
        lambda *args, **kwargs: "fired",
    )


def test_resolve_command_root_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = tmp_path / "configured"
    explicit = tmp_path / "explicit"

    monkeypatch.setenv(
        "MB_SCAN_CONTROL",
        str(configured),
    )

    assert resolve_command_root() == configured
    assert (
        resolve_command_root(explicit)
        == explicit
    )

    monkeypatch.delenv(
        "MB_SCAN_CONTROL",
        raising=False,
    )

    assert (
        resolve_command_root()
        == DEFAULT_COMMAND_ROOT
    )


def test_wait_until_resumed_stops_cleanly(
    tmp_path: Path,
) -> None:
    gate = ExportGate(tmp_path)
    gate.suspend(command_id="suspend-test")
    gate.close()

    stop_event = threading.Event()
    stop_event.set()

    assert (
        gate.wait_until_resumed(stop_event)
        is False
    )

    gate.resume(command_id="resume-test")


def test_runner_waits_when_gate_is_suspended(
    tmp_path: Path,
) -> None:
    stop_event = threading.Event()
    gate = FakeGate(
        suspended=True,
        wait_result=False,
    )
    exporter = RecordingExporter(stop_event)
    runner, state = make_runner(
        tmp_path,
        gate=gate,
        exporter=exporter,
        stop_event=stop_event,
    )

    runner.run_forever()

    assert gate.wait_calls == 1
    assert exporter.calls == []
    assert "exports_suspended" in (
        state.phases
    )
    assert state.phases[-1] == "stopping"


def test_slot_is_deflected_when_lock_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_fired_slot(monkeypatch)

    stop_event = threading.Event()
    gate = FakeGate(
        stop_on_try=stop_event,
    )
    exporter = RecordingExporter(stop_event)
    runner, state = make_runner(
        tmp_path,
        gate=gate,
        exporter=exporter,
        stop_event=stop_event,
    )

    runner.run_forever()

    assert gate.try_calls == 1
    assert exporter.calls == []
    assert state.phases[-1] == (
        "exports_suspended"
    )


def test_export_lease_is_released_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_fired_slot(monkeypatch)

    stop_event = threading.Event()
    lease = RecordingLease()
    gate = FakeGate(lease=lease)
    exporter = RecordingExporter(stop_event)
    runner, state = make_runner(
        tmp_path,
        gate=gate,
        exporter=exporter,
        stop_event=stop_event,
    )

    runner.run_forever()

    assert len(exporter.calls) == 1
    assert state.completed == [SLOT]
    assert lease.released is True


def test_export_lease_is_released_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_fired_slot(monkeypatch)

    stop_event = threading.Event()
    lease = RecordingLease()
    gate = FakeGate(lease=lease)
    exporter = RecordingExporter(
        stop_event,
        failure=RuntimeError(
            "simulated export failure"
        ),
    )
    runner, state = make_runner(
        tmp_path,
        gate=gate,
        exporter=exporter,
        stop_event=stop_event,
    )

    runner.run_forever()

    assert len(exporter.calls) == 1
    assert state.failures == [
        "RuntimeError: simulated export failure"
    ]
    assert lease.released is True
