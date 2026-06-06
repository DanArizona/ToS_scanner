# scan_main.py

from __future__ import annotations

import argparse
import logging
import os
import threading

from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from pynput import keyboard

from PySide6.QtCore import Slot, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from alerts import AlertManager, PushoverCredentials, load_pushover_credentials
from config import ScannerConfig, load_scanner_config
from exporter import ToSPseudoWidgetExporter
from heartbeat import HeartbeatThread
from run_state import SharedState, recover_previous_run
from scanner_logging import build_logger
from scan_runner import ScanRunner
from scheduler import SchedulerFlags, is_weekday
from startup_checks import StartupValidationError, fatal_startup, run_startup_checks
from scan_control_state import PauseController, UserScanRequest
from gui_support import (
    EscapeBridge,
    GuiLogBridge,
    ManagerBridge,
    QtCounterLogHandler,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

SLOT_SECONDS = (5, 20, 35, 50)
MARKET_OPEN = dt_time(9, 28, 0)
MARKET_CLOSE = dt_time(16, 2, 0)
USER_SCAN_MIN_LEAD_S = 7.0

DEFAULT_STATE_FILE = Path("./runtime/scan_runner_state.json")


# ---------------------------------------------------------------------------
# GUI control layer
# ---------------------------------------------------------------------------

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


def market_is_open_now_et() -> bool:
    now_et = datetime.now(ET)
    if not is_weekday(now_et):
        return False
    t = now_et.timetz().replace(tzinfo=None)
    return MARKET_OPEN <= t < MARKET_CLOSE


class ScanControlPanel(QWidget):
    def __init__(
        self,
        *,
        manager: ScanControlManager,
        logger: logging.Logger,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.logger = logger

        self.warning_count = 0
        self.error_count = 0
        self.exit_requested = False

        self.gui_log_bridge = GuiLogBridge()
        self.gui_log_bridge.log_level_seen.connect(self.on_log_level_seen)

        self.qt_counter_handler = QtCounterLogHandler(self.gui_log_bridge)
        self.logger.addHandler(self.qt_counter_handler)

        self.escape_bridge = EscapeBridge()
        self.escape_bridge.escape_pressed.connect(self.on_escape_pressed)
        self.escape_listener = keyboard.Listener(on_press=self._on_key_press)
        self.escape_listener.start()

        self.setWindowTitle("JTM Scan Manager")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(340, 460)
        self.move(20, 20)

        self._build_ui()
        self._apply_dark_theme()

        self.manager.bridge.running_changed.connect(self.on_running_changed)

        self.state_timer = QTimer(self)
        self.state_timer.timeout.connect(self.refresh_dynamic_state)
        self.state_timer.start(1000)

        self._refresh_counters()
        self.refresh_dynamic_state()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)

        title_label = QLabel("JTM Scan Manager")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont("Arial Black", 16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        outer.addWidget(title_label)

        file_label = QLabel(Path(__file__).name)
        file_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(file_label)

        top_row = QHBoxLayout()
        top_row.setSpacing(16)
        outer.addLayout(top_row)

        mode_group = QGroupBox("Mode")
        mode_layout = QVBoxLayout(mode_group)

        self.radio_production = QRadioButton("Production")
        self.radio_debug = QRadioButton("Debug")
        self.radio_production.setChecked(True)

        self.mode_group_buttons = QButtonGroup(self)
        self.mode_group_buttons.addButton(self.radio_production)
        self.mode_group_buttons.addButton(self.radio_debug)

        self.radio_production.toggled.connect(self.on_mode_changed)
        self.radio_debug.toggled.connect(self.on_mode_changed)

        mode_layout.addWidget(self.radio_production)
        mode_layout.addWidget(self.radio_debug)

        self.notify_checkbox = QCheckBox("Pushover notifications")
        self.notify_checkbox.setChecked(bool(self.manager.notifications_enabled))
        self.notify_checkbox.toggled.connect(self.on_notify_toggled)
        mode_layout.addWidget(self.notify_checkbox)

        top_row.addWidget(mode_group, stretch=0)

        maint_col = QVBoxLayout()
        maint_col.setSpacing(10)
        top_row.addLayout(maint_col, stretch=1)

        self.manual_init_btn = QPushButton("Manual init")
        self.manual_init_btn.clicked.connect(self.on_manual_init_clicked)
        maint_col.addWidget(self.manual_init_btn)

        self.unlock_scan_btn = QPushButton("Unlock scan")
        self.unlock_scan_btn.clicked.connect(self.on_unlock_scan_clicked)
        maint_col.addWidget(self.unlock_scan_btn)

        self.scan_btn = QPushButton("Scan")
        self.scan_btn.clicked.connect(self.on_scan_clicked)
        maint_col.addWidget(self.scan_btn)

        line1 = QFrame()
        line1.setFrameShape(QFrame.Shape.HLine)
        line1.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(line1)

        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(16)
        status_grid.setVerticalSpacing(10)
        outer.addLayout(status_grid)

        status_grid.addWidget(QLabel("Market status"), 0, 0)
        status_grid.addWidget(QLabel("Scan status"), 1, 0)
        status_grid.addWidget(QLabel("Warnings"), 2, 0)
        status_grid.addWidget(QLabel("Errors"), 3, 0)

        self.market_status_value = QLabel("Open")
        self.scan_status_value = QLabel("Stopped")
        self.warning_value = QLabel("0")
        self.error_value = QLabel("0")

        status_grid.addWidget(self.market_status_value, 0, 1)
        status_grid.addWidget(self.scan_status_value, 1, 1)
        status_grid.addWidget(self.warning_value, 2, 1)
        status_grid.addWidget(self.error_value, 3, 1)

        control_row = QHBoxLayout()
        control_row.setSpacing(12)
        outer.addLayout(control_row)

        self.start_btn = QPushButton("Start Scan")
        self.start_btn.clicked.connect(self.on_start_clicked)
        control_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop Scan")
        self.stop_btn.clicked.connect(self.on_stop_clicked)
        control_row.addWidget(self.stop_btn)

        esc_note = QLabel("ESC also requests\n a graceful stop")
        esc_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        control_row.addWidget(esc_note)

        line2 = QFrame()
        line2.setFrameShape(QFrame.Shape.HLine)
        line2.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(line2)

        self.exit_btn = QPushButton("Exit Scan Manager")
        self.exit_btn.clicked.connect(self.on_exit_clicked)
        outer.addWidget(self.exit_btn, alignment=Qt.AlignmentFlag.AlignCenter)

    def _apply_dark_theme(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background-color: #1f1f1f;
                color: #f2f2f2;
                font-size: 11pt;
            }

            QLabel {
                color: #f2f2f2;
            }

            QGroupBox {
                border: 1px solid #555555;
                border-radius: 4px;
                margin-top: 10px;
                padding-top: 10px;
                font-size: 10pt;
                color: #f2f2f2;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px 0 4px;
            }

            QPushButton {
                background-color: #3b3b3b;
                color: #f2f2f2;
                border: 1px solid #666666;
                border-radius: 4px;
                padding: 6px 10px;
                min-width: 88px;
            }

            QPushButton:hover {
                background-color: #4a4a4a;
            }

            QPushButton:disabled {
                background-color: #2b2b2b;
                color: #888888;
                border: 1px solid #444444;
            }

            QRadioButton {
                spacing: 6px;
            }

            QFrame[frameShape="4"],
            QFrame[frameShape="5"] {
                color: #666666;
            }
            """
        )

    def _on_key_press(self, key) -> None:
        if key == keyboard.Key.esc:
            self.escape_bridge.escape_pressed.emit()

    def _refresh_counters(self) -> None:
        self.warning_value.setText(str(self.warning_count))
        self.error_value.setText(str(self.error_count))

    def _set_exit_pending_ui(self) -> None:
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.exit_btn.setEnabled(False)

        self.radio_production.setEnabled(False)
        self.radio_debug.setEnabled(False)
        self.notify_checkbox.setEnabled(False)
        self.manual_init_btn.setEnabled(False)

        self.unlock_scan_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)
        self.scan_status_value.setText("Stopped")

    def _production_mode(self) -> bool:
        return self.radio_production.isChecked()

    def _compute_market_status_text(self) -> str:
        if not self._production_mode():
            return "NA"
        return "Open" if market_is_open_now_et() else "Closed"

    def _compute_scan_status_text(self) -> str:
        running = self.manager.is_running()

        if not running:
            return "Stopped"

        if not self._production_mode():
            return "Running"

        return "Running" if market_is_open_now_et() else "Wait"

    @Slot()
    def refresh_dynamic_state(self) -> None:
        market_text = self._compute_market_status_text()
        scan_text = self._compute_scan_status_text()

        self.market_status_value.setText(market_text)
        self.scan_status_value.setText(scan_text)

        if self.exit_requested:
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.exit_btn.setEnabled(False)
            self.radio_production.setEnabled(False)
            self.radio_debug.setEnabled(False)
            self.manual_init_btn.setEnabled(False)
            self.unlock_scan_btn.setEnabled(False)
            self.scan_btn.setEnabled(False)
            return

        running = self.manager.is_running()

        if running:
            self.radio_production.setEnabled(False)
            self.radio_debug.setEnabled(False)
            self.notify_checkbox.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        else:
            self.radio_production.setEnabled(True)
            self.radio_debug.setEnabled(True)
            self.notify_checkbox.setEnabled(True)
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)

        self.manual_init_btn.setEnabled(not self.exit_requested)
        self.unlock_scan_btn.setEnabled(not self.exit_requested)
        self.scan_btn.setEnabled(not self.exit_requested)
        self.exit_btn.setEnabled(True)

    @Slot(int)
    def on_log_level_seen(self, levelno: int) -> None:
        if levelno >= logging.ERROR:
            self.error_count += 1
        elif levelno >= logging.WARNING:
            self.warning_count += 1

        self._refresh_counters()

    @Slot()
    def on_mode_changed(self) -> None:
        if self.manager.is_running():
            return

        mode_name = "Production" if self._production_mode() else "Debug"
        self.logger.info("UI | Mode changed to %s", mode_name)
        self.refresh_dynamic_state()

    @Slot(bool)
    def on_notify_toggled(self, checked: bool) -> None:
        self.manager.set_notifications_enabled(bool(checked))

    @Slot()
    def on_manual_init_clicked(self) -> None:
        self.logger.info("UI | Manual init requested.")
        self.manager.manual_init()

    @Slot()
    def on_unlock_scan_clicked(self) -> None:
        self.logger.info("UI | Unlock scan requested.")
        self.manager.unlock_scan()

    @Slot()
    def on_scan_clicked(self) -> None:
        self.manager.request_user_scan()

    @Slot()
    def on_start_clicked(self) -> None:
        try:
            gate_active = self._production_mode()
            self.manager.start(gate_active=gate_active)
            self.refresh_dynamic_state()
        except StartupValidationError as exc:
            self.scan_status_value.setText("Stopped")
            QMessageBox.critical(self, "Startup Validation Error", str(exc))
        except Exception as exc:
            self.scan_status_value.setText("Stopped")
            self.logger.exception("Unexpected error during start: %s", exc)
            QMessageBox.critical(self, "Unexpected Error", str(exc))

    @Slot()
    def on_stop_clicked(self) -> None:
        self.manager.stop()
        self.refresh_dynamic_state()

    @Slot()
    def on_escape_pressed(self) -> None:
        if self.manager.is_running():
            self.logger.info("ESC | Stop requested by Escape key.")
            self.manager.stop()
            self.refresh_dynamic_state()

    @Slot()
    def on_exit_clicked(self) -> None:
        self.logger.info("UI | Exit Scan Manager requested.")

        if self.manager.is_running():
            self.exit_requested = True
            self.manager.stop()
            self._set_exit_pending_ui()
        else:
            app = QApplication.instance()
            if app is not None:
                app.quit()

    @Slot(bool)
    def on_running_changed(self, running: bool) -> None:
        self.refresh_dynamic_state()

        if self.exit_requested and not running:
            self.logger.info("UI | Runner stopped; exiting application.")
            app = QApplication.instance()
            if app is not None:
                app.quit()

    def closeEvent(self, event) -> None:
        if self.manager.is_running() and not self.exit_requested:
            self.logger.info("UI | Window close requested while runner active.")
            self.exit_requested = True
            self.manager.stop()
            self._set_exit_pending_ui()
            event.ignore()
            return

        try:
            self.state_timer.stop()
        except Exception:
            pass

        try:
            self.escape_listener.stop()
        except Exception:
            pass

        try:
            self.logger.removeHandler(self.qt_counter_handler)
            self.qt_counter_handler.close()
        except Exception:
            pass

        super().closeEvent(event)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run timed ToS scan exports with a small control panel."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for CSV files; defaults to MB_SCANS",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("./logs"),
        help="Directory for daily log files",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"Path to persistent runtime state json (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--layout-path",
        type=Path,
        default=None,
        help="Optional layout file for pseudo-widget definitions; defaults to MB_PWIDGET_YAML",
    )
    parser.add_argument(
        "--verify-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for CSV file to be verified after GUI export",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create stub CSVs instead of interacting with ToS",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    cfg = load_scanner_config()

    logger, listener = build_logger(args.log_dir)
    listener.start()

    app = QApplication([])

    try:
        logger.info("========================================================")
        logger.info("scan_main.py startup")
        logger.info("output_dir=%s", args.output_dir or cfg.scans_path)
        logger.info("log_dir=%s", args.log_dir)
        logger.info("state_file=%s", args.state_file)
        # logger.info("layout_path=%s", args.layout_path or Path(cfg.pwidget_yaml_path))
        logger.info("layout_path=%s", args.layout_path or cfg.pwidget_yaml_path)
        logger.info("dry_run=%s", args.dry_run)
        logger.info("window_tos_main prefix=%s", cfg.window_tos_main)

        manager = ScanControlManager(
            args=args,
            cfg=cfg,
            logger=logger,
        )

        panel = ScanControlPanel(
            manager=manager,
            logger=logger,
        )
        panel.show()

        rc = app.exec()
        manager.stop()
        return rc

    finally:
        listener.stop()


if __name__ == "__main__":
    raise SystemExit(main())

    