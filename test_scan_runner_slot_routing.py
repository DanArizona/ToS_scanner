from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import scan_runner
from scan_runner import (
    ScanRunner,
    scheduled_source_for_slot,
)
from scan_artifacts import (
    SOURCE_TOS_SCAN,
    SOURCE_WATCHLIST,
)


ET = ZoneInfo("America/New_York")


class State:
    def __init__(self) -> None:
        self.pending: list[tuple[datetime, Path]] = []
        self.completed: list[datetime] = []
        self.phases: list[str] = []
        self.failures: list[str] = []

    def touch(self, *, phase: str) -> None:
        self.phases.append(phase)

    def set_pending(
        self,
        slot: datetime,
        path: Path,
    ) -> None:
        self.pending.append((slot, path))

    def clear_pending(self, *, phase: str) -> None:
        self.phases.append(phase)

    def mark_completed(self, slot: datetime) -> None:
        self.completed.append(slot)

    def mark_failure(self, message: str) -> None:
        self.failures.append(message)


class Flags:
    def snapshot(self) -> tuple[bool, int]:
        return False, 0


class Pause:
    def is_paused(self) -> bool:
        return False

    def snapshot(self) -> tuple[bool, int]:
        return False, 0


class Lease:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


class Gate:
    def __init__(self, lease: Lease) -> None:
        self.lease = lease

    def is_suspended(self) -> bool:
        return False

    def try_begin_export(self) -> Lease:
        return self.lease


class Exporter:
    def __init__(
        self,
        stop_event: threading.Event,
    ) -> None:
        self.stop_event = stop_event
        self.calls: list[
            tuple[str, Path, datetime]
        ] = []

    def export_watchlist(
        self,
        path: Path,
        slot: datetime,
        *,
        stop_event: threading.Event,
    ) -> None:
        self.calls.append(
            ("watchlist", path, slot)
        )
        self.stop_event.set()

    def export_scan(
        self,
        path: Path,
        slot: datetime,
        *,
        stop_event: threading.Event,
    ) -> None:
        self.calls.append(
            ("scan", path, slot)
        )
        self.stop_event.set()


@pytest.mark.parametrize(
    (
        "second",
        "expected_kind",
        "expected_suffix",
    ),
    [
        (5, "watchlist", "-05-WL.csv"),
        (20, "watchlist", "-20-WL.csv"),
        (35, "watchlist", "-35-WL.csv"),
        (50, "scan", "-50-TS.csv"),
    ],
)
def test_runner_routes_each_scheduled_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    second: int,
    expected_kind: str,
    expected_suffix: str,
) -> None:
    slot = datetime(
        2026,
        8,
        7,
        10,
        0,
        second,
        tzinfo=ET,
    )
    stop_event = threading.Event()
    lease = Lease()
    exporter = Exporter(stop_event)
    state = State()

    monkeypatch.setattr(
        scan_runner,
        "next_slot_after",
        lambda now, gate_active: slot,
    )
    monkeypatch.setattr(
        scan_runner,
        "wait_until_dynamic",
        lambda *args, **kwargs: "fired",
    )

    runner = ScanRunner(
        exporter=exporter,
        logger=logging.getLogger(
            "test_scan_runner_slot_routing"
        ),
        shared_state=state,
        stop_event=stop_event,
        output_dir=tmp_path / "output",
        flags=Flags(),
        pause_ctl=Pause(),
        export_gate=Gate(lease),
    )

    runner.run_forever()

    assert len(exporter.calls) == 1
    kind, path, fired_slot = exporter.calls[0]
    assert kind == expected_kind
    assert str(path).endswith(expected_suffix)
    assert fired_slot == slot
    assert state.completed == [slot]
    assert lease.released is True


def test_unknown_scheduled_second_is_rejected() -> None:
    slot = datetime(
        2026,
        8,
        7,
        10,
        0,
        10,
        tzinfo=ET,
    )

    with pytest.raises(
        ValueError,
        match="Unsupported scheduled export second",
    ):
        scheduled_source_for_slot(slot)


def test_source_helper_assigns_expected_artifact_sources() -> None:
    for second in (5, 20, 35):
        slot = datetime(
            2026,
            8,
            7,
            10,
            0,
            second,
            tzinfo=ET,
        )
        assert (
            scheduled_source_for_slot(slot)
            == SOURCE_WATCHLIST
        )

    slot_50 = datetime(
        2026,
        8,
        7,
        10,
        0,
        50,
        tzinfo=ET,
    )
    assert (
        scheduled_source_for_slot(slot_50)
        == SOURCE_TOS_SCAN
    )
