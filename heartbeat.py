# heartbeat.py

"""Heartbeat monitoring thread for the ToS scanner.

This module defines the background heartbeat thread that watches shared scan
state for stale progress and triggers critical alerts when the runner appears
unresponsive.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from run_state import PersistentRunState, SharedState

if TYPE_CHECKING:
    from alerts import AlertManager


def stale_progress_requires_alert(
    snapshot: PersistentRunState,
    *,
    stale_for_s: float,
    stale_after_s: float,
    observed_at: datetime | None = None,
) -> bool:
    """Return whether stale progress represents an actionable runner stall.

    ``waiting_for_slot`` is expected to remain unchanged for long off-hours
    intervals.  In that phase, the pending slot plus the normal stale grace
    period is the deadline.  Missing or malformed pending-slot state fails
    closed and retains the ordinary stale-progress behavior.
    """

    if snapshot.critical_alert_sent or stale_for_s <= stale_after_s:
        return False

    if snapshot.phase != "waiting_for_slot":
        return True

    if not snapshot.pending_slot_et:
        return True

    try:
        pending_slot = datetime.fromisoformat(snapshot.pending_slot_et)
    except (TypeError, ValueError):
        return True

    if pending_slot.tzinfo is None or pending_slot.utcoffset() is None:
        return True

    now = observed_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")

    alert_after = pending_slot + timedelta(seconds=stale_after_s)
    return now.astimezone(timezone.utc) > alert_after.astimezone(timezone.utc)


class HeartbeatThread(threading.Thread):
    def __init__(
        self,
        *,
        logger: logging.Logger,
        shared_state: SharedState,
        alerts: AlertManager,
        stop_event: threading.Event,
        stale_after_s: float = 90.0,
        check_every_s: float = 5.0,
        fail_after_consecutive_errors: int = 3,
    ) -> None:
        super().__init__(name="Heartbeat", daemon=True)
        self.logger = logger
        self.shared_state = shared_state
        self.alerts = alerts
        self.stop_event = stop_event
        self.stale_after_s = stale_after_s
        self.check_every_s = check_every_s
        self.fail_after_consecutive_errors = fail_after_consecutive_errors

    # def run(self) -> None:
    #     self.logger.info("Heartbeat thread started.")
    #     while not self.stop_event.wait(self.check_every_s):
    #         snap = self.shared_state.snapshot()
    #         stale_for = self.shared_state.seconds_since_progress()

    #         if stale_for > self.stale_after_s and not snap.critical_alert_sent:
    #             msg = (
    #                 f"No progress for {stale_for:.1f}s. "
    #                 f"phase={snap.phase} pending={snap.pending_csv_path}"
    #             )
    #             self.shared_state.set_critical_alert_sent()
    #             self.alerts.critical("SCAN RUNNER HEARTBEAT ALERT", msg)
    #             self.stop_event.set()
    #             return

    #         if snap.consecutive_failures >= self.fail_after_consecutive_errors and not snap.critical_alert_sent:
    #             msg = (
    #                 f"Consecutive failures reached {snap.consecutive_failures}. "
    #                 f"last_error={snap.last_error}"
    #             )
    #             self.shared_state.set_critical_alert_sent()
    #             self.alerts.critical("SCAN RUNNER FAILURE ALERT", msg)
    #             self.stop_event.set()
    #             return

    def run(self) -> None:
        self.logger.info("Heartbeat thread started.")

        try:
            while not self.stop_event.wait(self.check_every_s):
                snap = self.shared_state.snapshot()
                stale_for = self.shared_state.seconds_since_progress()

                if stale_progress_requires_alert(
                    snap,
                    stale_for_s=stale_for,
                    stale_after_s=self.stale_after_s,
                ):
                    msg = (
                        f"No progress for {stale_for:.1f}s. "
                        f"phase={snap.phase} pending={snap.pending_csv_path}"
                    )
                    self.shared_state.set_critical_alert_sent()
                    self.alerts.critical("SCAN RUNNER HEARTBEAT ALERT", msg)
                    self.stop_event.set()
                    return

                if (
                    snap.consecutive_failures >= self.fail_after_consecutive_errors
                    and not snap.critical_alert_sent
                ):
                    msg = (
                        f"Consecutive failures reached {snap.consecutive_failures}. "
                        f"last_error={snap.last_error}"
                    )
                    self.shared_state.set_critical_alert_sent()
                    self.alerts.critical("SCAN RUNNER FAILURE ALERT", msg)
                    self.stop_event.set()
                    return

        finally:
            self.logger.info("Heartbeat thread stopped.")
