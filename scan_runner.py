# scan_runner.py

"""Main timed scan-runner loop.

This module owns the background scan loop that waits for scheduled slots,
coordinates pause/user-scan state, invokes the exporter, and updates persistent
runtime state.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from alerts import AlertManager
from exporter import ScanExporter
from run_state import SharedState
from scan_control_state import (
    PauseController,
    wait_until_dynamic,
    wait_while_paused,
)

from scheduler import ET, SchedulerFlags, next_slot_after

class ScanRunner:
    def __init__(
        self,
        *,
        exporter: ScanExporter,
        logger: logging.Logger,
        shared_state: SharedState,
        stop_event: threading.Event,
        output_dir: Path,
        flags: SchedulerFlags,
        pause_ctl: PauseController,
    ) -> None:
        self.exporter = exporter
        self.logger = logger
        self.shared_state = shared_state
        self.stop_event = stop_event
        self.output_dir = output_dir
        self.flags = flags
        self.pause_ctl = pause_ctl

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run_forever(self) -> None:
        self.logger.info("Scan runner entering main loop.")
        self.shared_state.touch(phase="idle")

        while not self.stop_event.is_set():
            if self.pause_ctl.is_paused():
                self.logger.info("Runner paused/deflected; waiting for maintenance action to complete.")
                self.shared_state.touch(phase="paused")
                if not wait_while_paused(self.stop_event, self.pause_ctl):
                    self.logger.info("Stop requested while paused.")
                    self.shared_state.clear_pending(phase="stopping")
                    return
                self.shared_state.touch(phase="idle")
                continue

            now_et = datetime.now(ET)
            gate_active, generation = self.flags.snapshot()
            paused, pause_generation = self.pause_ctl.snapshot()

            slot_et = next_slot_after(now_et, gate_active)
            csv_path = self.output_dir / f"scan-{slot_et:%Y-%m-%d-%H-%M-%S}-ToS.csv"

            self.logger.info(
                "Next slot=%s gate_active=%s paused=%s target_csv=%s",
                slot_et.isoformat(),
                gate_active,
                paused,
                csv_path,
            )

            self.shared_state.set_pending(slot_et, csv_path)
            self.shared_state.touch(phase="waiting_for_slot")

            wait_result = wait_until_dynamic(
                slot_et,
                self.stop_event,
                self.flags,
                generation,
                self.pause_ctl,
                pause_generation,
            )

            if wait_result == "stopped":
                self.logger.info("Stop requested before next slot fired.")
                self.shared_state.clear_pending(phase="stopping")
                return

            if wait_result == "recompute":
                self.logger.info("Scheduler state changed; recomputing next slot.")
                self.shared_state.clear_pending(phase="idle")
                continue

            if self.pause_ctl.is_paused():
                self.logger.info("Pause requested at slot boundary; deflecting export.")
                self.shared_state.clear_pending(phase="paused")
                continue

            try:
                self.shared_state.touch(phase="executing_gui")
                self.exporter.export_scan(csv_path, slot_et, stop_event=self.stop_event)

                self.shared_state.mark_completed(slot_et)

                self.shared_state.touch(phase="idle")

            except InterruptedError as exc:
                self.logger.warning("Export interrupted: %s", exc)
                return

            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                self.logger.exception("Slot execution failed: %s", msg)
                self.shared_state.mark_failure(msg)
