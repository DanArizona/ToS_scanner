# control_panel.py

"""Qt control panel for the ToS scanner.

This module defines the scanner GUI, including mode selection, notification
controls, manual actions, start/stop handling, status display, and keyboard
escape handling.
"""

from __future__ import annotations

import logging
from datetime import datetime

from pathlib import Path
from pynput import keyboard

from PySide6.QtCore import Slot, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QButtonGroup,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from control_manager import ScanControlManager
from gui_support import EscapeBridge, GuiLogBridge, QtCounterLogHandler
from scheduler import ET, MARKET_CLOSE, MARKET_OPEN, is_weekday
from startup_checks import StartupValidationError


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

    # @Slot()
    # def on_mode_changed(self) -> None:
    #     if self.manager.is_running():
    #         return

    #     mode_name = "Production" if self._production_mode() else "Debug"
    #     self.logger.info("UI | Mode changed to %s", mode_name)
    #     self.refresh_dynamic_state()

    @Slot(bool)
    def on_mode_changed(self, checked: bool) -> None:
        if not checked:
            return

        mode = "Production" if self.radio_production.isChecked() else "Debug"
        self.logger.info("UI | Mode changed to %s", mode)
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

