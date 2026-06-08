# control_manager.py

from __future__ import annotations

import argparse
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from alerts import AlertManager, PushoverCredentials, load_pushover_credentials
from config import ScannerConfig
from exporter import ToSPseudoWidgetExporter
from gui_support import ManagerBridge
from heartbeat import HeartbeatThread
from run_state import SharedState, recover_previous_run
from scan_control_state import PauseController, UserScanRequest
from scan_runner import ScanRunner
from scheduler import ET, SchedulerFlags
from startup_checks import StartupValidationError, run_startup_checks

USER_SCAN_MIN_LEAD_S = 7.0

class ScanControlManager:
    def __init__(
        self,
        *,
        args: argparse.Namespace,
        cfg: ScannerConfig,
        logger: logging.Logger,
    ) -> None:
        self.args = args
        self.cfg = cfg
        self.logger = logger

        script_dir = Path(__file__).resolve().parent
        self.layout_path = args.layout_path or cfg.pwidget_yaml_path
        self.output_dir = args.output_dir or cfg.scans_path

        self.bridge = ManagerBridge()

        self.stop_event: Optional[threading.Event] = None
        self.flags: Optional[SchedulerFlags] = None
        self.shared_state: Optional[SharedState] = None
        self.alerts: Optional[AlertManager] = None
        self.exporter: Optional[ToSPseudoWidgetExporter] = None
        self.runner: Optional[ScanRunner] = None
        self.heartbeat: Optional[HeartbeatThread] = None
        self.runner_thread: Optional[threading.Thread] = None

        self.pause_ctl = PauseController()
        self.maintenance_lock = threading.Lock()
        self.maintenance_busy = False
        self.user_scan_request = UserScanRequest()
        self.notifications_enabled = bool(cfg.notify_enable)

    def is_running(self) -> bool:
        return self.runner_thread is not None and self.runner_thread.is_alive()

    def set_gate_active(self, value: bool) -> None:
        if self.flags is not None:
            self.flags.set_gate_active(value)
            self.logger.info("UI | gate_active changed to %s", value)

    def _ensure_exporter(self) -> ToSPseudoWidgetExporter:
        if self.exporter is None:
            self.exporter = ToSPseudoWidgetExporter(
                logger=self.logger,
                cfg=self.cfg,
                layout_path=self.layout_path,
                dry_run=self.args.dry_run,
                verify_timeout_s=self.args.verify_timeout,
            )
        return self.exporter

    def _seconds_until_next_export(self) -> Optional[float]:
        if self.shared_state is None:
            return None

        snap = self.shared_state.snapshot()
        if not snap.pending_slot_et:
            return None

        try:
            target = datetime.fromisoformat(snap.pending_slot_et).astimezone(ET)
            return (target - datetime.now(ET)).total_seconds()
        except Exception:
            return None

    def _run_user_scan_now(self) -> None:
        try:
            exporter = self._ensure_exporter()
            exporter.perform_user_scan(stop_event=self.stop_event)
        except Exception:
            self.logger.exception("UI | Immediate user scan failed.")
        finally:
            self.user_scan_request.finish()

    def request_user_scan(self) -> None:
        self.logger.info("UI | Scan button clicked.")

        if self.maintenance_busy:
            self.logger.warning("UI | Scan request ignored because a maintenance action is active.")
            return

        # If runner is not active, just run immediately.
        if not self.is_running():
            if not self.user_scan_request.start_immediate():
                self.logger.warning("UI | Scan request ignored because one is already pending/active.")
                return

            thread = threading.Thread(
                target=self._run_user_scan_now,
                daemon=True,
                name="UserScanImmediate",
            )
            thread.start()
            return

        seconds_until = self._seconds_until_next_export()
        paused = self.pause_ctl.is_paused()

        if (
            seconds_until is not None
            and seconds_until >= USER_SCAN_MIN_LEAD_S
            and not paused
        ):
            if not self.user_scan_request.start_immediate():
                self.logger.warning("UI | Scan request ignored because one is already pending/active.")
                return

            self.logger.info(
                "UI | Scan request will run immediately. seconds_until_next_export=%.2f",
                seconds_until,
            )

            thread = threading.Thread(
                target=self._run_user_scan_now,
                daemon=True,
                name="UserScanImmediate",
            )
            thread.start()
            return

        if self.user_scan_request.request_deferred():
            self.logger.info(
                "UI | Scan request deferred until after the upcoming export. seconds_until_next_export=%s paused=%s",
                "None" if seconds_until is None else f"{seconds_until:.2f}",
                paused,
            )
        else:
            self.logger.warning("UI | Scan request ignored because one is already pending/active.")

    def _run_maintenance_action(self, label: str, fn) -> None:
        with self.maintenance_lock:
            if self.maintenance_busy:
                self.logger.warning("UI | Maintenance action '%s' ignored because another is active.", label)
                return
            self.maintenance_busy = True

        self.logger.info("UI | Maintenance action requested: %s", label)
        self.pause_ctl.set_paused(True)

        try:
            exporter = self._ensure_exporter()
            fn(exporter.controller)

        except Exception:
            self.logger.exception("UI | Maintenance action failed: %s", label)

        finally:
            self.pause_ctl.set_paused(False)
            self.maintenance_busy = False
            self.logger.info("UI | Maintenance action finished: %s", label)

    def manual_init(self) -> None:
        thread = threading.Thread(
            target=self._run_maintenance_action,
            args=("manual_init", lambda ctl: ctl.manual_init()),
            daemon=True,
            name="ManualInit",
        )
        thread.start()

    def unlock_scan(self) -> None:
        def _do_unlock(ctl):
            unlocked = ctl.unlock_scan()
            self.logger.info("UI | Unlock scan result unlocked=%s", unlocked)

        thread = threading.Thread(
            target=self._run_maintenance_action,
            args=("unlock_scan", _do_unlock),
            daemon=True,
            name="UnlockScan",
        )
        thread.start()

    def start(self, *, gate_active: bool) -> None:
        if self.is_running():
            self.logger.info("UI | Start ignored because runner is already active.")
            return

        run_startup_checks(
            logger=self.logger,
            cfg=self.cfg,
            layout_path=self.layout_path,
            output_dir=self.output_dir,
        )

        self.shared_state = SharedState(self.args.state_file, logger=self.logger)

        pushover_credentials: Optional[PushoverCredentials] = None

        if self.notifications_enabled:
            pushover_credentials = load_pushover_credentials(self.cfg)
            if pushover_credentials is None:
                raise StartupValidationError(
                    "Pushover notifications are enabled, but credentials are not configured yet. "
                    f"Expected encrypted credentials at: {self.cfg.pushover_ecfg_path}"
                )

        self.alerts = AlertManager(
            self.logger,
            notifications_enabled=self.notifications_enabled,
            pushover_credentials=pushover_credentials,
        )

        recover_previous_run(self.logger, self.shared_state)

        self.stop_event = threading.Event()
        self.flags = SchedulerFlags(gate_active=gate_active)

        self.exporter = ToSPseudoWidgetExporter(
            logger=self.logger,
            cfg=self.cfg,
            layout_path=self.layout_path,
            dry_run=self.args.dry_run,
            verify_timeout_s=self.args.verify_timeout,
        )

        self.runner = ScanRunner(
            exporter=self.exporter,
            logger=self.logger,
            shared_state=self.shared_state,
            stop_event=self.stop_event,
            output_dir=self.output_dir,
            flags=self.flags,
            pause_ctl=self.pause_ctl,
            user_scan_request=self.user_scan_request,
        )

        self.heartbeat = HeartbeatThread(
            logger=self.logger,
            shared_state=self.shared_state,
            alerts=self.alerts,
            stop_event=self.stop_event,
            stale_after_s=90.0,
            check_every_s=5.0,
            fail_after_consecutive_errors=3,
        )
        self.heartbeat.start()

        self.runner_thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="ScanRunnerMain",
        )
        self.runner_thread.start()

        self.logger.info("UI | Scan loop started. gate_active=%s", gate_active)
        self.bridge.running_changed.emit(True)
        self.bridge.status_changed.emit("Running")

    def _run_loop(self) -> None:
        try:
            assert self.runner is not None
            self.runner.run_forever()

        except Exception as exc:
            self.logger.exception("Fatal error in runner thread.")
            if self.alerts is not None:
                self.alerts.critical("SCAN RUNNER FATAL ERROR", str(exc))

        finally:
            if self.stop_event is not None:
                self.stop_event.set()

            self.bridge.running_changed.emit(False)
            self.bridge.status_changed.emit("Stopped")
            self.logger.info("UI | Scan loop stopped.")

    def stop(self) -> None:
        if not self.is_running():
            self.logger.info("UI | Stop ignored because runner is not active.")
            return

        self.logger.info("UI | Stop requested.")
        if self.stop_event is not None:
            self.stop_event.set()
        self.bridge.status_changed.emit("Stopping...")

    def set_notifications_enabled(self, value: bool) -> None:
        self.notifications_enabled = bool(value)
        self.logger.info(
            "UI | notifications_enabled changed to %s",
            self.notifications_enabled,
        )
