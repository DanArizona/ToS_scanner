# scanner_logging.py

"""Queue-based logging setup for the ToS scanner.

This module configures console and daily rotating file logging through a logging
queue, allowing worker threads to log safely through the main scanner logger.
"""

from __future__ import annotations

import logging
import logging.handlers
import queue
from datetime import datetime
from pathlib import Path
from typing import Optional


class DailyFileHandler(logging.Handler):
    """
    Opens the file for the current day on first emit and switches files
    when the date changes.

    File name format:
        scan-YYYY-MM-DD-ToS.log
    """

    def __init__(self, log_dir: Path) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: Optional[str] = None
        self._delegate: Optional[logging.FileHandler] = None

    def _ensure_handler(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")

        if self._delegate is not None and self._current_date == today:
            return

        if self._delegate is not None:
            self._delegate.close()

        filename = self.log_dir / f"scan-{today}-ToS.log"
        self._delegate = logging.FileHandler(filename, encoding="utf-8")
        self._delegate.setFormatter(self.formatter)
        self._current_date = today

    def emit(self, record: logging.LogRecord) -> None:
        self.acquire()
        try:
            self._ensure_handler()
            assert self._delegate is not None
            self._delegate.emit(record)
        except Exception:
            self.handleError(record)
        finally:
            self.release()

    def close(self) -> None:
        self.acquire()
        try:
            if self._delegate is not None:
                self._delegate.close()
                self._delegate = None
        finally:
            self.release()
            super().close()


def build_logger(log_dir: Path) -> tuple[logging.Logger, logging.handlers.QueueListener]:
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()

    logger = logging.getLogger("scan_runner")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    logger.addHandler(logging.handlers.QueueHandler(log_queue))

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = DailyFileHandler(log_dir)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    listener = logging.handlers.QueueListener(
        log_queue,
        file_handler,
        console_handler,
        respect_handler_level=True,
    )

    return logger, listener
