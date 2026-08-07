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
from export_gate import (
    ExportActionLock,
    ExportGate,
    resolve_command_root,
)
from exporter import ScanExporter
from run_state import SharedState
from scan_control_state import (
    PauseController,
    wait_until_dynamic,
    wait_while_paused,
)
from scan_artifacts import (
    SOURCE_TOS_SCAN,
    SOURCE_WATCHLIST,
    ScanArtifactSpec,
)
from scheduler import (
    ET,
    SchedulerFlags,
    next_slot_after,
)


WATCHLIST_SLOT_SECONDS = frozenset({5, 20, 35})
SCANNER_SLOT_SECOND = 50


def scheduled_source_for_slot(
    slot_et: datetime,
) -> str:
    """Return the output source assigned to one scheduled slot."""

    if slot_et.second in WATCHLIST_SLOT_SECONDS:
        return SOURCE_WATCHLIST

    if slot_et.second == SCANNER_SLOT_SECOND:
        return SOURCE_TOS_SCAN

    raise ValueError(
        "Unsupported scheduled export second: "
        f"{slot_et.second}. Expected one of "
        "05, 20, 35, or 50."
    )


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
        export_gate: ExportGate | None = None,
    ) -> None:
        self.exporter = exporter
        self.logger = logger
        self.shared_state = shared_state
        self.stop_event = stop_event
        self.output_dir = output_dir
        self.flags = flags
        self.pause_ctl = pause_ctl
        self.export_gate = (
            export_gate
            if export_gate is not None
            else ExportGate(
                resolve_command_root()
            )
        )
        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

    def run_forever(self) -> None:
        self.logger.info(
            "Scan runner entering main loop."
        )
        self.shared_state.touch(phase="idle")

        while not self.stop_event.is_set():
            if self.export_gate.is_suspended():
                self.logger.info(
                    "Scheduled exports are suspended; "
                    "waiting for the shared export gate."
                )
                self.shared_state.touch(
                    phase="exports_suspended"
                )

                if not self.export_gate.wait_until_resumed(
                    self.stop_event
                ):
                    self.logger.info(
                        "Stop requested while exports "
                        "were suspended."
                    )
                    self.shared_state.clear_pending(
                        phase="stopping"
                    )
                    return

                self.logger.info(
                    "Scheduled exports resumed."
                )
                self.shared_state.touch(
                    phase="idle"
                )
                continue

            if self.pause_ctl.is_paused():
                self.logger.info(
                    "Runner paused/deflected; waiting "
                    "for maintenance action to complete."
                )
                self.shared_state.touch(
                    phase="paused"
                )

                if not wait_while_paused(
                    self.stop_event,
                    self.pause_ctl,
                ):
                    self.logger.info(
                        "Stop requested while paused."
                    )
                    self.shared_state.clear_pending(
                        phase="stopping"
                    )
                    return

                self.shared_state.touch(
                    phase="idle"
                )
                continue

            now_et = datetime.now(ET)
            gate_active, generation = (
                self.flags.snapshot()
            )
            paused, pause_generation = (
                self.pause_ctl.snapshot()
            )

            slot_et = next_slot_after(
                now_et,
                gate_active,
            )
            source_name = scheduled_source_for_slot(
                slot_et
            )
            csv_path = (
                self.output_dir
                / ScanArtifactSpec(
                    source_name
                ).filename_for(slot_et)
            )

            self.logger.info(
                "Next slot=%s source=%s gate_active=%s "
                "paused=%s target_csv=%s",
                slot_et.isoformat(),
                source_name,
                gate_active,
                paused,
                csv_path,
            )

            self.shared_state.set_pending(
                slot_et,
                csv_path,
            )
            self.shared_state.touch(
                phase="waiting_for_slot"
            )

            wait_result = wait_until_dynamic(
                slot_et,
                self.stop_event,
                self.flags,
                generation,
                self.pause_ctl,
                pause_generation,
            )

            if wait_result == "stopped":
                self.logger.info(
                    "Stop requested before next slot fired."
                )
                self.shared_state.clear_pending(
                    phase="stopping"
                )
                return

            if wait_result == "recompute":
                self.logger.info(
                    "Scheduler state changed; "
                    "recomputing next slot."
                )
                self.shared_state.clear_pending(
                    phase="idle"
                )
                continue

            if self.pause_ctl.is_paused():
                self.logger.info(
                    "Pause requested at slot boundary; "
                    "deflecting export."
                )
                self.shared_state.clear_pending(
                    phase="paused"
                )
                continue

            export_lease: (
                ExportActionLock | None
            ) = self.export_gate.try_begin_export()

            if export_lease is None:
                self.logger.info(
                    "Scheduled export deflected by "
                    "the shared export gate."
                )
                self.shared_state.clear_pending(
                    phase="exports_suspended"
                )

                if self.stop_event.wait(0.10):
                    return

                continue

            try:
                self.shared_state.touch(
                    phase="executing_gui"
                )

                if source_name == SOURCE_WATCHLIST:
                    self.exporter.export_watchlist(
                        csv_path,
                        slot_et,
                        stop_event=self.stop_event,
                    )
                else:
                    self.exporter.export_scan(
                        csv_path,
                        slot_et,
                        stop_event=self.stop_event,
                    )

                self.shared_state.mark_completed(
                    slot_et
                )
                self.shared_state.touch(
                    phase="idle"
                )

            except InterruptedError as exc:
                self.logger.warning(
                    "Export interrupted: %s",
                    exc,
                )
                return

            except Exception as exc:
                message = (
                    f"{type(exc).__name__}: {exc}"
                )
                self.logger.exception(
                    "Slot execution failed: %s",
                    message,
                )
                self.shared_state.mark_failure(
                    message
                )

            finally:
                export_lease.release()
