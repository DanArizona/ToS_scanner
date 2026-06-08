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

from alerts import AlertManager
from run_state import SharedState


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

    def run(self) -> None:
        self.logger.info("Heartbeat thread started.")
        while not self.stop_event.wait(self.check_every_s):
            snap = self.shared_state.snapshot()
            stale_for = self.shared_state.seconds_since_progress()

            if stale_for > self.stale_after_s and not snap.critical_alert_sent:
                msg = (
                    f"No progress for {stale_for:.1f}s. "
                    f"phase={snap.phase} pending={snap.pending_csv_path}"
                )
                self.shared_state.set_critical_alert_sent()
                self.alerts.critical("SCAN RUNNER HEARTBEAT ALERT", msg)
                self.stop_event.set()
                return

            if snap.consecutive_failures >= self.fail_after_consecutive_errors and not snap.critical_alert_sent:
                msg = (
                    f"Consecutive failures reached {snap.consecutive_failures}. "
                    f"last_error={snap.last_error}"
                )
                self.shared_state.set_critical_alert_sent()
                self.alerts.critical("SCAN RUNNER FAILURE ALERT", msg)
                self.stop_event.set()
                return
