# gui_support.py

"""Small Qt bridge and logging helper classes for the scanner GUI.

This module contains QObject signal bridges and a logging handler used to safely
communicate between scanner logic, logging, and the Qt control panel.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal


class ManagerBridge(QObject):
    status_changed = Signal(str)
    running_changed = Signal(bool)


class EscapeBridge(QObject):
    escape_pressed = Signal()


class GuiLogBridge(QObject):
    log_level_seen = Signal(int)


class QtCounterLogHandler(logging.Handler):
    def __init__(self, bridge: GuiLogBridge) -> None:
        super().__init__()
        self.bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bridge.log_level_seen.emit(int(record.levelno))
        except Exception:
            pass