from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from heartbeat import stale_progress_requires_alert
from run_state import PersistentRunState


UTC = timezone.utc


def waiting_snapshot(slot: datetime | str | None) -> PersistentRunState:
    slot_text = slot.isoformat() if isinstance(slot, datetime) else slot
    return PersistentRunState(
        phase="waiting_for_slot",
        pending_slot_et=slot_text,
        pending_csv_path="C:/stockScans/pending.csv",
    )


def test_future_scheduled_slot_is_not_stale_progress() -> None:
    observed = datetime(2026, 9, 19, 22, 0, tzinfo=UTC)
    pending = datetime(2026, 9, 21, 13, 24, 5, tzinfo=UTC)

    assert not stale_progress_requires_alert(
        waiting_snapshot(pending),
        stale_for_s=172_800.0,
        stale_after_s=90.0,
        observed_at=observed,
    )


def test_waiting_slot_uses_stale_grace_after_due_time() -> None:
    pending = datetime(2026, 9, 21, 13, 24, 5, tzinfo=UTC)

    assert not stale_progress_requires_alert(
        waiting_snapshot(pending),
        stale_for_s=172_800.0,
        stale_after_s=90.0,
        observed_at=pending + timedelta(seconds=90),
    )

    assert stale_progress_requires_alert(
        waiting_snapshot(pending),
        stale_for_s=172_800.0,
        stale_after_s=90.0,
        observed_at=pending + timedelta(seconds=90.001),
    )


@pytest.mark.parametrize("pending_slot", [None, "not-a-timestamp"])
def test_invalid_waiting_slot_state_fails_closed(
    pending_slot: str | None,
) -> None:
    assert stale_progress_requires_alert(
        waiting_snapshot(pending_slot),
        stale_for_s=90.001,
        stale_after_s=90.0,
        observed_at=datetime(2026, 9, 19, 22, 0, tzinfo=UTC),
    )


def test_other_runner_phases_keep_normal_stale_threshold() -> None:
    snapshot = PersistentRunState(phase="exporting")
    observed = datetime(2026, 9, 19, 22, 0, tzinfo=UTC)

    assert not stale_progress_requires_alert(
        snapshot,
        stale_for_s=90.0,
        stale_after_s=90.0,
        observed_at=observed,
    )
    assert stale_progress_requires_alert(
        snapshot,
        stale_for_s=90.001,
        stale_after_s=90.0,
        observed_at=observed,
    )


def test_existing_critical_alert_is_not_repeated() -> None:
    snapshot = PersistentRunState(
        phase="exporting",
        critical_alert_sent=True,
    )

    assert not stale_progress_requires_alert(
        snapshot,
        stale_for_s=180.0,
        stale_after_s=90.0,
        observed_at=datetime(2026, 9, 19, 22, 0, tzinfo=UTC),
    )
