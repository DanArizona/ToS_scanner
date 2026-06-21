# test_tos_pwidget_menu.py

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from pynput import keyboard
from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from config import load_scanner_config
from tos_pwidget_actions import ToSActionsController


def setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"tos-pwidget-menu-{datetime.now():%Y-%m-%d}.log"
    logger = logging.getLogger("tos_pwidget_menu")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        logger.handlers.clear()

    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(file_fmt)

    ch = logging.StreamHandler()
    ch.setFormatter(console_fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def make_filename() -> str:
    return f"scan-{datetime.now():%Y-%m-%d-%H-%M-%S}-ToS.csv"


class ActionsMenuDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.selected_action: Optional[int] = None
        self.setWindowTitle("ToS PWidget Actions")
        self.setModal(True)
        self.resize(420, 260)

        outer = QVBoxLayout(self)

        label = QLabel(
            "Choose a pwidget action.\n"
            "This dialog closes immediately after a button is pressed."
        )
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(label)

        grid = QGridLayout()
        outer.addLayout(grid)

        actions = [
            (1, "1  Open Scan Tab"),
            (2, "2  Load scan050_data"),
            (3, "3  Load % Change Gainers"),
            (4, "4  Trigger Scan"),
            (5, "5  Export CSV File"),
            (6, "6  Enter Filename"),
            (7, "7  Confirm Save"),
            (8, "8  Cancel Export"),
            (9, "9  Verify Save"),
            (10, "10  NOP"),
        ]

        for idx, (action_id, text) in enumerate(actions):
            row = idx // 2
            col = idx % 2
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked=False, aid=action_id: self.choose(aid))
            grid.addWidget(btn, row, col)

    def choose(self, action_id: int) -> None:
        self.selected_action = action_id
        self.accept()


class HotkeyBridge(QObject):
    show_menu_requested = Signal()
    quit_requested = Signal()

class ActionsMenuApp(QObject):
    def __init__(
        self,
        *,
        controller: ToSActionsController,
        target_dir: Path,
        logger: logging.Logger,
        app: QApplication,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.target_dir = target_dir
        self.logger = logger
        self.app = app

        self.current_filename: Optional[str] = None
        self.menu_open = False

        self.bridge = HotkeyBridge()
        self.bridge.show_menu_requested.connect(self.show_menu)
        self.bridge.quit_requested.connect(self.quit_app)

        self.menu_hotkey = keyboard.HotKey(
            keyboard.HotKey.parse("<ctrl>+<alt>+m"),
            self._on_menu_hotkey,
        )

        self.quit_hotkey = keyboard.HotKey(
            keyboard.HotKey.parse("<ctrl>+<alt>+q"),
            self._on_quit_hotkey,
        )

        self.listener = keyboard.Listener(
            on_press=self._for_canonical(self._on_press),
            on_release=self._for_canonical(self._on_release),
        )
        self.listener.start()

        self.logger.info("Actions menu app started.")
        self.logger.info("Global trigger: Ctrl+Alt+m")
        self.logger.info("Quit trigger:   Ctrl+Alt+q")
        self.logger.info("Target directory: %s", self.target_dir)

    def _for_canonical(self, f):
        return lambda k: f(self.listener.canonical(k))

    def _on_press(self, key) -> None:
        self.menu_hotkey.press(key)
        self.quit_hotkey.press(key)

    def _on_release(self, key) -> None:
        self.menu_hotkey.release(key)
        self.quit_hotkey.release(key)

    def _on_menu_hotkey(self) -> None:
        self.logger.info("HOTKEY | Ctrl+Alt+m detected")
        self.bridge.show_menu_requested.emit()

    def _on_quit_hotkey(self) -> None:
        self.logger.info("HOTKEY | Ctrl+Alt+q detected")
        self.bridge.quit_requested.emit()

    @Slot()
    def quit_app(self) -> None:
        self.logger.info("APP | quit requested")
        self.stop()
        self.app.quit()

    def stop(self) -> None:
        try:
            self.listener.stop()
        except Exception:
            pass
        self.logger.info("Actions menu app stopped.")

    @Slot()
    def show_menu(self) -> None:
        if self.menu_open:
            self.logger.info("MENU | request ignored because menu is already open")
            return

        self.menu_open = True
        self.logger.info("MENU | opening dialog")

        try:
            dlg = ActionsMenuDialog()
            result = dlg.exec()

            if result == QDialog.DialogCode.Accepted and dlg.selected_action is not None:                
                action_id = dlg.selected_action
                self.logger.info("MENU | selected action %s", action_id)
                threading.Thread(
                    target=self.execute_action,
                    args=(action_id,),
                    daemon=True,
                    name=f"Action-{action_id}",
                ).start()
            else:
                self.logger.info("MENU | dialog cancelled")
        finally:
            self.menu_open = False

    def execute_action(self, action_id: int) -> None:
        try:
            self.logger.info("ACTION | begin %s", action_id)

            if action_id == 1:
                self.controller.open_scan_tab()

            elif action_id == 2:
                self.controller.load_scan50_query()

            elif action_id == 3:
                self.controller.load_pct_gainers_query()

            elif action_id == 4:
                self.controller.trigger_scan()

            elif action_id == 5:
                self.controller.export_csv_file()

            elif action_id == 6:
                self.current_filename = make_filename()
                self.logger.info("ACTION | current filename set to %s", self.current_filename)
                self.controller.enter_filename(self.current_filename, self.target_dir)

            elif action_id == 7:
                self.controller.confirm_save()

            elif action_id == 8:
                self.controller.cancel_export()

            elif action_id == 9:
                if not self.current_filename:
                    self.logger.warning("VERIFY | no current filename is set yet")
                    return

                ok = self.controller.verify_save(
                    self.target_dir,
                    self.current_filename,
                )
                self.logger.info(
                    "VERIFY | filename=%s result=%s",
                    self.current_filename,
                    ok,
                )

            elif action_id == 10:
                self.controller.nop()

            else:
                self.logger.warning("ACTION | unknown action id: %s", action_id)

            self.logger.info("ACTION | end %s", action_id)

        except Exception:
            self.logger.exception("ACTION | failed for action %s", action_id)


def main() -> int:
    cfg = load_scanner_config()
    logger = setup_logger(Path("logs"))

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    controller = ToSActionsController(
        layout_path=cfg.pwidget_yaml_path,
        cfg=cfg,
        logger=logger,
    )

    target_dir = cfg.scans_path

    menu_app = ActionsMenuApp(
        controller=controller,
        target_dir=target_dir,
        logger=logger,
        app=app,
    )

    try:
        return app.exec()
    finally:
        menu_app.stop()


if __name__ == "__main__":
    raise SystemExit(main())

