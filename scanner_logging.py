# scanner_logging.py

"""Logging adapter for the ToS scanner.

The scanner uses the queue-based logging implementation from mb_tools, but keeps
a small local adapter so the rest of the application does not depend directly on
the mb_tools logging API.
"""

from __future__ import annotations

import logging
from pathlib import Path

from mb_tools.logging_queue import (
    LoggingQueueManager,
    get_logger,
    setup_logging,
    shutdown_logging,
)


class ScannerLoggingRuntime:
    """
    Compatibility wrapper for the scanner's previous QueueListener object.

    mb_tools.logging_queue.setup_logging() starts the queue listener immediately,
    while the scanner's older code expected build_logger() to return an object
    with start() and stop() methods. This wrapper preserves that call pattern.
    """

    def __init__(self, manager: LoggingQueueManager) -> None:
        self.manager = manager
        self._stopped = False

    def start(self) -> None:
        """
        Compatibility no-op.

        setup_logging() already starts the queue listener, so existing scanner
        code may call start() without accidentally starting a second listener.
        """
        return

    def stop(self) -> None:
        """
        Stop queued logging and restore the root logger.

        Safe to call more than once.
        """
        if self._stopped:
            return

        self._stopped = True
        shutdown_logging(restore_root=True)


def build_logger(log_dir: Path) -> tuple[logging.Logger, ScannerLoggingRuntime]:
    """
    Configure queue-based scanner logging.

    Returns
    -------
    tuple[logging.Logger, ScannerLoggingRuntime]
        A named scanner logger and a compatibility runtime object with start()
        and stop() methods.
    """
    manager = setup_logging(
        log_dir=log_dir,
        app_name="scan-ToS",
        level=logging.INFO,
        console=True,
        clear_existing_root_handlers=True,
        capture_warnings=True,
    )

    logger = get_logger("scan_runner")
    logger.setLevel(logging.INFO)
    logger.propagate = True

    logger.info("Scanner logger initialized through mb_tools.logging_queue")
    logger.info("ALL log file: %s", manager.all_log_path)
    logger.info("MAIN log file: %s", manager.main_log_path)

    return logger, ScannerLoggingRuntime(manager)







# ----------------------------------- OBSOLETE -----------------------------------

# """Queue-based logging setup for the ToS scanner.

# This module configures console and daily rotating file logging through a logging
# queue, allowing worker threads to log safely through the main scanner logger.
# """

# from __future__ import annotations

# import logging
# import logging.handlers
# import queue
# from datetime import datetime
# from pathlib import Path
# from typing import Optional


# class DailyFileHandler(logging.Handler):
#     """
#     Opens the file for the current day on first emit and switches files
#     when the date changes.

#     File name format:
#         scan-YYYY-MM-DD-ToS.log
#     """

#     def __init__(self, log_dir: Path) -> None:
#         super().__init__()
#         self.log_dir = log_dir
#         self.log_dir.mkdir(parents=True, exist_ok=True)
#         self._current_date: Optional[str] = None
#         self._delegate: Optional[logging.FileHandler] = None

#     def _ensure_handler(self) -> None:
#         today = datetime.now().strftime("%Y-%m-%d")

#         if self._delegate is not None and self._current_date == today:
#             return

#         if self._delegate is not None:
#             self._delegate.close()

#         filename = self.log_dir / f"scan-{today}-ToS.log"
#         self._delegate = logging.FileHandler(filename, encoding="utf-8")
#         self._delegate.setFormatter(self.formatter)
#         self._current_date = today

#     def emit(self, record: logging.LogRecord) -> None:
#         self.acquire()
#         try:
#             self._ensure_handler()
#             assert self._delegate is not None
#             self._delegate.emit(record)
#         except Exception:
#             self.handleError(record)
#         finally:
#             self.release()

#     def close(self) -> None:
#         self.acquire()
#         try:
#             if self._delegate is not None:
#                 self._delegate.close()
#                 self._delegate = None
#         finally:
#             self.release()
#             super().close()


# def build_logger(log_dir: Path) -> tuple[logging.Logger, logging.handlers.QueueListener]:
#     log_queue: queue.Queue[logging.LogRecord] = queue.Queue()

#     logger = logging.getLogger("scan_runner")
#     logger.setLevel(logging.INFO)
#     logger.handlers.clear()
#     logger.propagate = False

#     logger.addHandler(logging.handlers.QueueHandler(log_queue))

#     fmt = logging.Formatter(
#         fmt="%(asctime)s | %(levelname)-8s | %(threadName)s | %(message)s",
#         datefmt="%Y-%m-%d %H:%M:%S",
#     )

#     file_handler = DailyFileHandler(log_dir)
#     file_handler.setLevel(logging.INFO)
#     file_handler.setFormatter(fmt)

#     console_handler = logging.StreamHandler()
#     console_handler.setLevel(logging.INFO)
#     console_handler.setFormatter(fmt)

#     listener = logging.handlers.QueueListener(
#         log_queue,
#         file_handler,
#         console_handler,
#         respect_handler_level=True,
#     )

#     return logger, listener
