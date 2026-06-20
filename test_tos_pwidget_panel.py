# test_tos_pwidget_panel.py

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config import load_scanner_config
from tos_pwidget_actions import ToSDebugController


def setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"tos-debug-panel-{datetime.now():%Y-%m-%d}.log"

    logger = logging.getLogger("tos_debug_panel")
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


class PanelBridge(QObject):
    action_finished = Signal()
    log_line = Signal(str)
    filename_changed = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, bridge: PanelBridge) -> None:
        super().__init__()
        self.bridge = bridge
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.bridge.log_line.emit(msg)
        except Exception:
            pass


class DebugPanel(QWidget):
    def __init__(
        self,
        *,
        controller: ToSDebugController,
        layout_path: Path,
        target_dir: Path,
        logger: logging.Logger,
    ) -> None:
        super().__init__()

        self.controller = controller
        self.layout_path = Path(layout_path)        
        self.target_dir = target_dir
        self.logger = logger
        self.current_filename: Optional[str] = None
        self.busy = False

        self.bridge = PanelBridge()
        self.bridge.action_finished.connect(self.on_action_finished)
        self.bridge.log_line.connect(self.append_log_line)
        self.bridge.filename_changed.connect(self.set_filename_text)

        self.qt_log_handler = QtLogHandler(self.bridge)
        self.logger.addHandler(self.qt_log_handler)

        self.setWindowTitle("ToS Actions Panel")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(760, 520)

        self._build_ui()

        self.logger.info("Debug panel started.")
        self.logger.info("Layout YAML: %s", self.layout_path)        
        self.logger.info("Target directory: %s", self.target_dir)

    def closeEvent(self, event) -> None:
        try:
            self.logger.info("Debug panel closing.")
            self.logger.removeHandler(self.qt_log_handler)
            self.qt_log_handler.close()
        except Exception:
            pass
        super().closeEvent(event)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        title = QLabel("ToS Actions Panel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        outer.addWidget(title)

        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.status_label)

        self.filename_label = QLabel("Current CSV filename:")
        outer.addWidget(self.filename_label)

        self.filename_value = QLabel("<none>")
        self.filename_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filename_value.setStyleSheet("font-family: Consolas, monospace;")
        outer.addWidget(self.filename_value)

        layout_row = QHBoxLayout()
        outer.addLayout(layout_row)

        layout_row.addWidget(QLabel("Layout YAML:"))

        self.layout_path_edit = QLineEdit(str(self.layout_path))
        self.layout_path_edit.setMinimumWidth(520)
        layout_row.addWidget(self.layout_path_edit, stretch=1)

        self.layout_browse_btn = QPushButton("Browse")
        self.layout_browse_btn.clicked.connect(self.browse_layout_yaml)
        layout_row.addWidget(self.layout_browse_btn)

        self.layout_reload_btn = QPushButton("Reload")
        self.layout_reload_btn.clicked.connect(self.reload_layout_yaml)
        layout_row.addWidget(self.layout_reload_btn)

        # self.target_dir_label = QLabel(f"Target dir: {self.target_dir}")
        # self.target_dir_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # outer.addWidget(self.target_dir_label)

        target_row = QHBoxLayout()
        outer.addLayout(target_row)

        target_row.addWidget(QLabel("Target dir:"))

        self.target_dir_edit = QLineEdit(str(self.target_dir))
        self.target_dir_edit.setMinimumWidth(520)
        target_row.addWidget(self.target_dir_edit, stretch=1)

        self.target_browse_btn = QPushButton("Browse")
        self.target_browse_btn.clicked.connect(self.browse_target_dir)
        target_row.addWidget(self.target_browse_btn)

        grid = QGridLayout()
        outer.addLayout(grid)

        self.buttons: list[QPushButton] = []

        action_specs = [
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
            (11, "11  Select WL Default"),
            (12, "12  Select WL scan50_data"),
            (13, "13  Open WL Export"),
            (14, "14 Setup Export Dir"),
        ]

        for idx, (action_id, text) in enumerate(action_specs):
            row = idx // 2
            col = idx % 2
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked=False, aid=action_id: self.start_action(aid))
            grid.addWidget(btn, row, col)
            self.buttons.append(btn)

        bottom = QHBoxLayout()
        outer.addLayout(bottom)

        # self.sequence_btn = QPushButton("Export → Enter Pathname → Confirm Save → Verify Save")
        self.sequence_btn = QPushButton("Complete Save Sequence")
        self.sequence_btn.clicked.connect(self.start_export_sequence)
        bottom.addWidget(self.sequence_btn)

        self.quit_btn = QPushButton("Quit")
        self.quit_btn.clicked.connect(self.close)
        bottom.addWidget(self.quit_btn)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        outer.addWidget(self.log_view, stretch=1)

    def set_busy(self, busy: bool, text: str) -> None:
        self.busy = busy
        self.status_label.setText(text)
        for btn in self.buttons:
            btn.setEnabled(not busy)
        self.sequence_btn.setEnabled(not busy)

    def append_log_line(self, text: str) -> None:
        self.log_view.append(text)

    def set_filename_text(self, filename: str) -> None:
        self.filename_value.setText(filename)



    def browse_layout_yaml(self) -> None:
        start_path = str(Path(self.layout_path_edit.text()).parent)

        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select pseudo-widget layout YAML",
            start_path,
            "YAML files (*.yaml *.yml);;All files (*.*)",
        )

        if selected:
            self.layout_path_edit.setText(selected)
            self.logger.info("PANEL | selected layout YAML: %s", selected)


    def reload_layout_yaml(self) -> None:
        if self.busy:
            self.logger.info("PANEL | reload layout ignored because panel is busy")
            return

        new_path = Path(self.layout_path_edit.text()).expanduser().resolve()

        if not new_path.is_file():
            self.logger.warning("PANEL | layout YAML does not exist: %s", new_path)
            self.status_label.setText("Layout YAML not found")
            return

        try:
            self.set_busy(True, "Reloading layout YAML ...")
            self.controller = ToSDebugController(
                layout_path=new_path,
                cfg=self.controller.cfg,
                logger=self.logger,
            )
            self.layout_path = new_path
            self.layout_path_edit.setText(str(new_path))
            self.logger.info("PANEL | reloaded layout YAML: %s", new_path)
            self.status_label.setText("Layout reloaded")
        except Exception:
            self.logger.exception("PANEL | failed to reload layout YAML: %s", new_path)
            self.status_label.setText("Layout reload failed")
        finally:
            self.set_busy(False, "Ready")


    def browse_target_dir(self) -> None:
        start_path = self.target_dir_edit.text().strip()

        if not start_path:
            start_path = str(self.target_dir)

        selected = QFileDialog.getExistingDirectory(
            self,
            "Select target directory for scan CSV files",
            start_path,
        )

        if selected:
            self.target_dir_edit.setText(selected)
            self.target_dir = Path(selected).expanduser().resolve()
            self.logger.info("PANEL | selected target directory: %s", self.target_dir)


    def current_target_dir(self) -> Path:
        raw_text = self.target_dir_edit.text().strip()

        if not raw_text:
            raise RuntimeError("Target directory is blank.")

        target_dir = Path(raw_text).expanduser().resolve()

        if not target_dir.is_dir():
            raise RuntimeError(f"Target directory does not exist: {target_dir}")

        self.target_dir = target_dir
        return target_dir


    def start_action(self, action_id: int) -> None:
        if self.busy:
            self.logger.info("PANEL | action %s ignored because panel is busy", action_id)
            return

        self.set_busy(True, f"Running action {action_id} ...")
        self.logger.info("PANEL | action %s requested", action_id)

        thread = threading.Thread(
            target=self.execute_action,
            args=(action_id,),
            daemon=True,
            name=f"Action-{action_id}",
        )
        thread.start()

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
                target_dir = self.current_target_dir()

                self.current_filename = make_filename()
                self.logger.info("ACTION | current filename set to %s", self.current_filename)
                self.bridge.filename_changed.emit(self.current_filename)

                self.controller.enter_filename(self.current_filename, target_dir)

            elif action_id == 7:
                self.controller.confirm_save()

            elif action_id == 8:
                self.controller.cancel_export()

            elif action_id == 9:
                filename = self.current_filename
                # if not self.current_filename:
                if not filename:
                    # self.logger.warning("VERIFY | no current filename is set yet")
                    # return
                    raise RuntimeError("No current CSV filename to verify.")

                # ok = self.controller.verify_save(
                #     self.target_dir,
                #     self.current_filename,
                # )
                ok = self.controller.verify_save(self.current_target_dir(), filename)

                if ok:
                    self.status_label.setText("Save verified")
                else:
                    self.status_label.setText("Save not verified")

                self.logger.info(
                    "VERIFY | filename=%s result=%s",
                    self.current_filename,
                    ok,
                )

            elif action_id == 10:
                self.controller.nop()

            elif action_id == 11:
                self.controller.select_watchlist_default()

            elif action_id == 12:
                self.controller.select_watchlist_scan50_data()

            elif action_id == 13:
                self.controller.open_watchlist_export()

            elif action_id == 14:
                target_dir = self.current_target_dir()

                self.current_filename = make_filename()
                self.logger.info("ACTION | current filename set to %s", self.current_filename)
                self.bridge.filename_changed.emit(self.current_filename)

                self.controller.enter_filename_then_export_directory(
                    self.current_filename,
                    target_dir,
                )

            else:
                self.logger.warning("ACTION | unknown action id: %s", action_id)

            self.logger.info("ACTION | end %s", action_id)

        except Exception:
            self.logger.exception("ACTION | failed for action %s", action_id)

        finally:
            self.bridge.action_finished.emit()

    def start_export_sequence(self) -> None:
        if self.busy:
            self.logger.info("PANEL | export sequence ignored because panel is busy")
            return

        self.set_busy(True, "Running export/save sequence ...")
        self.logger.info("PANEL | export/save sequence requested")

        thread = threading.Thread(
            target=self.execute_export_sequence,
            daemon=True,
            name="Action-Sequence",
        )
        thread.start()

    def execute_export_sequence(self) -> None:
        try:
            self.logger.info("SEQUENCE | begin export/save sequence")

            self.controller.export_csv_file()

            self.current_filename = make_filename()
            self.logger.info("SEQUENCE | current filename set to %s", self.current_filename)
            self.bridge.filename_changed.emit(self.current_filename)

            self.controller.enter_filename(self.current_filename, self.target_dir)
            self.controller.confirm_save()

            ok = self.controller.verify_save(
                self.target_dir,
                self.current_filename,
            )

            self.logger.info(
                "SEQUENCE | verify result filename=%s result=%s",
                self.current_filename,
                ok,
            )

            if not ok:
                self.logger.warning("SEQUENCE | save verification failed")

            self.logger.info("SEQUENCE | end export/save sequence")

        except Exception:
            self.logger.exception("SEQUENCE | failed during export/save sequence")

        finally:
            self.bridge.action_finished.emit()

    @Slot()
    def on_action_finished(self) -> None:
        self.set_busy(False, "Ready")
        self.logger.info("PANEL | action finished")


def main() -> int:
    cfg = load_scanner_config()
    logger = setup_logger(Path("logs"))

    app = QApplication([])

    controller = ToSDebugController(
        layout_path=cfg.pwidget_yaml_path,
        cfg=cfg,
        logger=logger,
    )

    target_dir = cfg.scans_path

    panel = DebugPanel(
        controller=controller,
        layout_path=Path(cfg.pwidget_yaml_path),
        target_dir=target_dir,
        logger=logger,
    )
    panel.show()
    panel.raise_()
    panel.activateWindow()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

