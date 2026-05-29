# scan_main.py

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import urllib.parse
import urllib.request
import winsound
import uuid

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Optional, Protocol
from zoneinfo import ZoneInfo

import pygetwindow as gw
from pynput import keyboard

from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# from PySide6.QtCore import QObject, Signal, Slot, Qt
# from PySide6.QtWidgets import (
#     QApplication,
#     QCheckBox,
#     QHBoxLayout,
#     QLabel,
#     QMessageBox,
#     QPushButton,
#     QVBoxLayout,
#     QWidget,
# )

from config import WindowConfig
from layout import load_widget_layout
from tos_debug_actions import ToSDebugController


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

SLOT_SECONDS = (5, 20, 35, 50)
MARKET_OPEN = dt_time(9, 28, 0)
MARKET_CLOSE = dt_time(16, 2, 0)

DEFAULT_STATE_FILE = Path("./runtime/scan_runner_state.json")
PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

REQUIRED_ENV_VARS = (
    "PUSHOVER_APP_TOKEN",
    "PUSHOVER_USER_KEY",
)


# ---------------------------------------------------------------------------
# Persistent / shared state
# ---------------------------------------------------------------------------

@dataclass
class PersistentRunState:
    phase: str = "startup"
    pending_slot_et: Optional[str] = None
    pending_csv_path: Optional[str] = None
    last_completed_slot_et: Optional[str] = None
    last_progress_utc: Optional[str] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    critical_alert_sent: bool = False


class SharedState:
    def __init__(
        self,
        state_file: Path,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._lock = threading.Lock()
        self.state_file = state_file
        self.logger = logger
        self.model = PersistentRunState()
        self.last_progress_monotonic = time.monotonic()

        self._persist_failures = 0
        self._dirty = False

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.load()

    def load(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.model = PersistentRunState(**data)
        except Exception:
            self.model = PersistentRunState(
                phase="startup",
                last_error="Could not parse existing state file.",
            )

    def _save_locked(self) -> None:
        """
        Save while self._lock is already held.

        Uses a unique temp file and retries os.replace() on transient
        PermissionError so bookkeeping failures do not easily kill the scan loop.
        """
        self.model.last_progress_utc = datetime.now(UTC).isoformat()
        payload = json.dumps(asdict(self.model), indent=2)

        last_exc: Optional[Exception] = None

        for attempt in range(8):
            tmp = self.state_file.with_name(
                f"{self.state_file.stem}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
            )

            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, self.state_file)
                return

            except PermissionError as exc:
                last_exc = exc
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                time.sleep(0.05 * (attempt + 1))

            except Exception as exc:
                last_exc = exc
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                break

        if last_exc is not None:
            raise last_exc

    def _save_best_effort_locked(self, context: str) -> None:
        """
        Try to persist state, but do not raise if persistence fails.
        The scan loop should continue even if bookkeeping is temporarily blocked.
        """
        try:
            self._save_locked()
            if self._persist_failures > 0 and self.logger:
                self.logger.warning(
                    "State persistence recovered after %d failure(s). context=%s",
                    self._persist_failures,
                    context,
                )
            self._persist_failures = 0
            self._dirty = False

        except Exception as exc:
            self._persist_failures += 1
            self._dirty = True

            if self.logger:
                self.logger.error(
                    "State persistence failed (count=%d). context=%s error=%s",
                    self._persist_failures,
                    context,
                    exc,
                )

    def save(self) -> None:
        with self._lock:
            self._save_best_effort_locked("save")

    def touch(self, *, phase: Optional[str] = None) -> None:
        with self._lock:
            if phase is not None:
                self.model.phase = phase
            self.last_progress_monotonic = time.monotonic()
            self._save_best_effort_locked(f"touch phase={phase!r}")

    def set_pending(self, slot_et: datetime, csv_path: Path) -> None:
        with self._lock:
            self.model.phase = "pending_slot"
            self.model.pending_slot_et = slot_et.isoformat()
            self.model.pending_csv_path = str(csv_path)
            self.model.last_error = None
            self.last_progress_monotonic = time.monotonic()
            self._save_best_effort_locked(f"set_pending slot={slot_et.isoformat()}")

    def clear_pending(self, *, phase: str = "idle") -> None:
        with self._lock:
            self.model.phase = phase
            self.model.pending_slot_et = None
            self.model.pending_csv_path = None
            self.last_progress_monotonic = time.monotonic()
            self._save_best_effort_locked(f"clear_pending phase={phase!r}")

    def mark_completed(self, slot_et: datetime) -> None:
        with self._lock:
            self.model.phase = "completed_slot"
            self.model.last_completed_slot_et = slot_et.isoformat()
            self.model.pending_slot_et = None
            self.model.pending_csv_path = None
            self.model.last_error = None
            self.model.consecutive_failures = 0
            self.last_progress_monotonic = time.monotonic()
            self._save_best_effort_locked(f"mark_completed slot={slot_et.isoformat()}")

    def mark_failure(self, message: str) -> None:
        with self._lock:
            self.model.phase = "error"
            self.model.last_error = message
            self.model.consecutive_failures += 1
            self.last_progress_monotonic = time.monotonic()
            self._save_best_effort_locked("mark_failure")

    def clear_pending_as_missed(self, message: str) -> None:
        with self._lock:
            self.model.phase = "missed_slot"
            self.model.last_error = message
            self.model.pending_slot_et = None
            self.model.pending_csv_path = None
            self.last_progress_monotonic = time.monotonic()
            self._save_best_effort_locked("clear_pending_as_missed")

    def snapshot(self) -> PersistentRunState:
        with self._lock:
            return PersistentRunState(**asdict(self.model))

    def seconds_since_progress(self) -> float:
        with self._lock:
            return time.monotonic() - self.last_progress_monotonic

    def set_critical_alert_sent(self) -> None:
        with self._lock:
            self.model.critical_alert_sent = True
            self._save_best_effort_locked("set_critical_alert_sent")


# class SharedState:
#     def __init__(self, state_file: Path) -> None:
#         self._lock = threading.Lock()
#         self.state_file = state_file
#         self.model = PersistentRunState()
#         self.last_progress_monotonic = time.monotonic()
#         self.state_file.parent.mkdir(parents=True, exist_ok=True)
#         self.load()

#     def load(self) -> None:
#         if not self.state_file.exists():
#             return
#         try:
#             data = json.loads(self.state_file.read_text(encoding="utf-8"))
#             self.model = PersistentRunState(**data)
#         except Exception:
#             self.model = PersistentRunState(
#                 phase="startup",
#                 last_error="Could not parse existing state file.",
#             )

#     def save(self) -> None:
#         with self._lock:
#             self.model.last_progress_utc = datetime.now(UTC).isoformat()
#             tmp = self.state_file.with_suffix(".tmp")
#             tmp.write_text(
#                 json.dumps(asdict(self.model), indent=2),
#                 encoding="utf-8",
#             )
#             tmp.replace(self.state_file)

#     def touch(self, *, phase: Optional[str] = None) -> None:
#         with self._lock:
#             if phase is not None:
#                 self.model.phase = phase
#             self.last_progress_monotonic = time.monotonic()
#         self.save()

#     def set_pending(self, slot_et: datetime, csv_path: Path) -> None:
#         with self._lock:
#             self.model.phase = "pending_slot"
#             self.model.pending_slot_et = slot_et.isoformat()
#             self.model.pending_csv_path = str(csv_path)
#             self.model.last_error = None
#             self.last_progress_monotonic = time.monotonic()
#         self.save()

#     def clear_pending(self, *, phase: str = "idle") -> None:
#         with self._lock:
#             self.model.phase = phase
#             self.model.pending_slot_et = None
#             self.model.pending_csv_path = None
#             self.last_progress_monotonic = time.monotonic()
#         self.save()

#     def mark_completed(self, slot_et: datetime) -> None:
#         with self._lock:
#             self.model.phase = "completed_slot"
#             self.model.last_completed_slot_et = slot_et.isoformat()
#             self.model.pending_slot_et = None
#             self.model.pending_csv_path = None
#             self.model.last_error = None
#             self.model.consecutive_failures = 0
#             self.last_progress_monotonic = time.monotonic()
#         self.save()

#     def mark_failure(self, message: str) -> None:
#         with self._lock:
#             self.model.phase = "error"
#             self.model.last_error = message
#             self.model.consecutive_failures += 1
#             self.last_progress_monotonic = time.monotonic()
#         self.save()

#     def clear_pending_as_missed(self, message: str) -> None:
#         with self._lock:
#             self.model.phase = "missed_slot"
#             self.model.last_error = message
#             self.model.pending_slot_et = None
#             self.model.pending_csv_path = None
#             self.last_progress_monotonic = time.monotonic()
#         self.save()

#     def snapshot(self) -> PersistentRunState:
#         with self._lock:
#             return PersistentRunState(**asdict(self.model))

#     def seconds_since_progress(self) -> float:
#         with self._lock:
#             return time.monotonic() - self.last_progress_monotonic

#     def set_critical_alert_sent(self) -> None:
#         with self._lock:
#             self.model.critical_alert_sent = True
#         self.save()


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class DailyFileHandler(logging.Handler):
    """
    Opens the file for the current day on first emit and switches files when the
    date changes. File name format: scan-YYYY-MM-DD-ToS.log
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


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

class StartupValidationError(RuntimeError):
    pass


def fatal_startup(logger: logging.Logger, message: str, exit_code: int = 2) -> int:
    logger.error(message)
    print(f"ERROR: {message}", file=sys.stderr, flush=True)
    return exit_code


def validate_required_env_vars(logger: logging.Logger) -> None:
    missing: list[str] = []
    for name in REQUIRED_ENV_VARS:
        value = os.environ.get(name)
        if value is None or not value.strip():
            missing.append(name)

    if missing:
        raise StartupValidationError(
            "Required environment variable(s) not set: " + ", ".join(missing)
        )

    logger.info("Startup check passed: required Pushover environment variables are set.")


def get_matching_window(title_prefix: str):
    normalized_prefix = title_prefix.strip()

    for win in gw.getAllWindows():
        title = (win.title or "").strip()
        if title.startswith(normalized_prefix):
            return win

    return None


def validate_win_main_open(logger: logging.Logger, cfg: WindowConfig):
    title_prefix = cfg.WINDOW_TOS_MAIN
    win = get_matching_window(title_prefix)

    if win is None:
        raise StartupValidationError(
            f"Required ToS window is not open/visible: win_main startswith {title_prefix!r}"
        )

    if int(win.width) <= 0 or int(win.height) <= 0:
        raise StartupValidationError(
            f"Matched ToS window has invalid size: title={win.title!r} size={win.width}x{win.height}"
        )

    logger.info("Startup check passed: win_main is open: actual title=%s", win.title)
    return win


def extract_expected_size_from_layout(layout, widget_name: str) -> tuple[int, int]:
    item = None

    if isinstance(layout, dict):
        item = layout.get(widget_name)
    else:
        item = getattr(layout, widget_name, None)

    if item is None:
        raise StartupValidationError(
            f"Could not find widget '{widget_name}' in loaded YAML layout."
        )

    candidates = [
        item,
        getattr(item, "root", None),
        getattr(item, "region", None),
        getattr(item, "bbox", None),
    ]

    for obj in candidates:
        if obj is None:
            continue

        w = getattr(obj, "w", None)
        h = getattr(obj, "h", None)

        if w is None:
            w = getattr(obj, "width", None)
        if h is None:
            h = getattr(obj, "height", None)

        if w is not None and h is not None:
            return int(w), int(h)

    raise StartupValidationError(
        f"Could not extract width/height for widget '{widget_name}' from YAML layout."
    )


def validate_win_main_size(
    logger: logging.Logger,
    cfg: WindowConfig,
    layout,
    win_main_window,
) -> None:
    expected_w, expected_h = extract_expected_size_from_layout(layout, "win_main")
    tolerance_px = int(cfg.WINDOW_ALL_MAX_DIMS_ERR)

    actual_w = int(win_main_window.width)
    actual_h = int(win_main_window.height)

    dw = abs(actual_w - expected_w)
    dh = abs(actual_h - expected_h)

    if dw > tolerance_px or dh > tolerance_px:
        raise StartupValidationError(
            "win_main size check failed. "
            f"Expected approximately {expected_w}x{expected_h} from YAML, "
            f"actual on-screen size is {actual_w}x{actual_h}, "
            f"tolerance is +/-{tolerance_px}px, "
            f"delta=({dw}, {dh})."
        )

    logger.info(
        "Startup check passed: win_main size ok. expected=%sx%s actual=%sx%s tolerance=%spx",
        expected_w,
        expected_h,
        actual_w,
        actual_h,
        tolerance_px,
    )


def validate_output_dir_access(logger: logging.Logger, output_dir: Path) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".__scan_write_test.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except Exception as exc:
        raise StartupValidationError(
            f"Output directory is not accessible/writable: {output_dir} | {exc}"
        ) from exc

    logger.info("Startup check passed: output directory is accessible: %s", output_dir)


def run_startup_checks(
    logger: logging.Logger,
    cfg: WindowConfig,
    layout_path: Optional[Path],
    output_dir: Path,
) -> None:
    validate_required_env_vars(logger)

    win_main_window = validate_win_main_open(logger, cfg)

    if layout_path is None:
        raise StartupValidationError(
            "Layout path is required for win_main dimension validation."
        )

    try:
        layout = load_widget_layout(layout_path, cfg.TITLE_MAP)
    except Exception as exc:
        raise StartupValidationError(
            f"Could not load layout YAML '{layout_path}': {exc}"
        ) from exc

    validate_win_main_size(logger, cfg, layout, win_main_window)
    validate_output_dir_access(logger, output_dir)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class AlertManager:
    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def popup(self, title: str, message: str) -> None:
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                message,
                title,
                0x10 | 0x1000,  # MB_ICONHAND | MB_SYSTEMMODAL
            )
        except Exception as exc:
            self.logger.exception("Popup dialog failed: %s", exc)

    def play_alert_sound(self) -> None:
        try:
            winsound.MessageBeep(winsound.MB_ICONHAND)
            winsound.Beep(1200, 500)
            winsound.Beep(1000, 700)
        except Exception as exc:
            self.logger.exception("Alert sound failed: %s", exc)

    def send_pushover(self, title: str, message: str) -> None:
        app_token = os.environ.get("PUSHOVER_APP_TOKEN")
        user_key = os.environ.get("PUSHOVER_USER_KEY")

        if not app_token or not user_key:
            self.logger.warning(
                "Pushover skipped: PUSHOVER_APP_TOKEN and/or PUSHOVER_USER_KEY not set."
            )
            return

        payload = urllib.parse.urlencode(
            {
                "token": app_token,
                "user": user_key,
                "title": title,
                "message": message,
                "priority": 1,
            }
        ).encode("utf-8")

        try:
            req = urllib.request.Request(PUSHOVER_API_URL, data=payload, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            self.logger.info("Pushover sent successfully. Response=%s", body)
        except Exception as exc:
            self.logger.exception("Pushover send failed: %s", exc)

    def critical(self, title: str, message: str) -> None:
        self.logger.critical("%s | %s", title, message)
        self.play_alert_sound()
        self.send_pushover(title, message)
        self.popup(title, message)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

class SchedulerFlags:
    def __init__(self, gate_active: bool = True) -> None:
        self._lock = threading.Lock()
        self._gate_active = gate_active
        self._generation = 0

    def snapshot(self) -> tuple[bool, int]:
        with self._lock:
            return self._gate_active, self._generation

    def get_gate_active(self) -> bool:
        with self._lock:
            return self._gate_active

    def set_gate_active(self, value: bool) -> None:
        with self._lock:
            if self._gate_active != value:
                self._gate_active = value
                self._generation += 1


def is_weekday(dt_et: datetime) -> bool:
    return dt_et.weekday() < 5


def next_slot_after(now_et: datetime, gate_active: bool) -> datetime:
    """
    Return the next slot at :05, :20, :35, :50.

    If gate_active is True:
        restrict to weekday core market session.
    If gate_active is False:
        run continuously on the slot cadence, regardless of market hours.
    """
    candidate = now_et.replace(microsecond=0) + timedelta(seconds=1)

    if not gate_active:
        while candidate.second not in SLOT_SECONDS:
            candidate += timedelta(seconds=1)
        return candidate

    while True:
        if is_weekday(candidate) and candidate.timetz().replace(tzinfo=None) < MARKET_OPEN:
            candidate = candidate.replace(
                hour=MARKET_OPEN.hour,
                minute=MARKET_OPEN.minute,
                second=0,
                microsecond=0,
            )

        if (not is_weekday(candidate)) or candidate.timetz().replace(tzinfo=None) >= MARKET_CLOSE:
            candidate = (candidate + timedelta(days=1)).replace(
                hour=MARKET_OPEN.hour,
                minute=MARKET_OPEN.minute,
                second=0,
                microsecond=0,
            )
            while not is_weekday(candidate):
                candidate = (candidate + timedelta(days=1)).replace(
                    hour=MARKET_OPEN.hour,
                    minute=MARKET_OPEN.minute,
                    second=0,
                    microsecond=0,
                )
            continue

        if candidate.second in SLOT_SECONDS:
            return candidate

        candidate += timedelta(seconds=1)


def wait_until_dynamic(
    target: datetime,
    stop_event: threading.Event,
    flags: SchedulerFlags,
    expected_generation: int,
) -> str:
    """
    Returns:
      - "fired"
      - "stopped"
      - "recompute"
    """
    while not stop_event.is_set():
        _, generation = flags.snapshot()
        if generation != expected_generation:
            return "recompute"

        remaining = (target - datetime.now(ET)).total_seconds()
        if remaining <= 0:
            return "fired"

        time.sleep(min(remaining, 0.25))

    return "stopped"


# ---------------------------------------------------------------------------
# Recovery logic
# ---------------------------------------------------------------------------

def recover_previous_run(logger: logging.Logger, shared_state: SharedState) -> None:
    snap = shared_state.snapshot()
    if not snap.pending_csv_path:
        return

    pending_csv = Path(snap.pending_csv_path)
    logger.warning(
        "Recovery check: previous run had pending slot=%s csv=%s",
        snap.pending_slot_et,
        pending_csv,
    )

    if pending_csv.exists():
        logger.warning(
            "Recovery: pending CSV already exists. Marking previous slot completed."
        )
        slot_et = datetime.fromisoformat(snap.pending_slot_et).astimezone(ET)  # type: ignore[arg-type]
        shared_state.mark_completed(slot_et)
    else:
        logger.warning(
            "Recovery: pending CSV does not exist. Marking slot missed and continuing."
        )
        shared_state.clear_pending_as_missed(
            "Previous run ended before CSV was verified."
        )


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

class ScanExporter(Protocol):
    def export_scan(
        self,
        csv_path: Path,
        slot_et: datetime,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        ...


class ToSPseudoWidgetExporter:
    """
    Reuses the tested ToSDebugController sequence:
      export_csv_file()
      enter_filename(filename, target_dir)
      confirm_save()
      verify_save(target_dir, filename)
    """

    def __init__(
        self,
        *,
        logger: logging.Logger,
        cfg: WindowConfig,
        layout_path: Optional[Path] = None,
        dry_run: bool = False,
        verify_timeout_s: float = 10.0,
    ) -> None:
        self.logger = logger
        self.cfg = cfg
        self.dry_run = dry_run
        self.verify_timeout_s = verify_timeout_s

        self.controller = ToSDebugController(
            layout_path=layout_path or cfg.WIDGET_STACK_YAML,
            cfg=cfg,
            logger=logger,
        )

        # Production-ish timing/jitter, independent of the slower debug panel settings
        self.controller.ENABLE_RANDOM_TIMING = True
        self.controller.ENABLE_RANDOM_POSITION = True
        self.controller.EXTRA_DELAY_MAX_S = 0.005
        self.controller.JITTER_X_PX = 5
        self.controller.JITTER_Y_PX = 3
        self.controller.MOVE_DURATION_S = 0.08
        self.controller.SEGMENT_DURATION_S = 0.06
        self.controller.CLICK_PAUSE_S = 0.08
        self.controller.STEP_PAUSE_S = 0.15

    def _check_stop(self, stop_event: Optional[threading.Event]) -> None:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Stop requested during export sequence.")

    def export_scan(
        self,
        csv_path: Path,
        slot_et: datetime,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self.logger.info("GUI | Begin export for slot %s", slot_et.isoformat())
        self.logger.info("GUI | Target CSV filename: %s", csv_path.name)

        if self.dry_run:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_text(
                "Symbol,%Change,Volume,Last\nTEST,+0.00%,1000,1.23\n",
                encoding="utf-8",
            )
            self.logger.info("DRY RUN | Stub CSV created at %s", csv_path)
            return

        self._check_stop(stop_event)
        self.controller.export_csv_file()

        self._check_stop(stop_event)
        self.controller.enter_filename(csv_path.name, csv_path.parent)

        self._check_stop(stop_event)
        self.controller.confirm_save()

        self._check_stop(stop_event)
        ok = self.controller.verify_save(
            csv_path.parent,
            csv_path.name,
            timeout_s=self.verify_timeout_s,
        )

        if not ok:
            raise TimeoutError(f"CSV file was not verified within timeout: {csv_path}")

        self.logger.info("CSV output verified: %s", csv_path)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

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
    ) -> None:
        self.exporter = exporter
        self.logger = logger
        self.shared_state = shared_state
        self.stop_event = stop_event
        self.output_dir = output_dir
        self.flags = flags

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run_forever(self) -> None:
        self.logger.info("Scan runner entering main loop.")
        self.shared_state.touch(phase="idle")

        while not self.stop_event.is_set():
            now_et = datetime.now(ET)
            gate_active, generation = self.flags.snapshot()
            slot_et = next_slot_after(now_et, gate_active)
            csv_path = self.output_dir / f"scan-{slot_et:%Y-%m-%d-%H-%M-%S}-ToS.csv"

            self.logger.info(
                "Next slot=%s gate_active=%s target_csv=%s",
                slot_et.isoformat(),
                gate_active,
                csv_path,
            )

            self.shared_state.set_pending(slot_et, csv_path)
            self.shared_state.touch(phase="waiting_for_slot")

            wait_result = wait_until_dynamic(
                slot_et,
                self.stop_event,
                self.flags,
                generation,
            )

            if wait_result == "stopped":
                self.logger.info("Stop requested before next slot fired.")
                self.shared_state.clear_pending(phase="stopping")
                return

            if wait_result == "recompute":
                self.logger.info("Scheduler gate state changed; recomputing next slot.")
                self.shared_state.clear_pending(phase="idle")
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


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

class HeartbeatThread(threading.Thread):
    def __init__(
        self,
        *,
        logger: logging.Logger,
        shared_state: SharedState,
        alerts: AlertManager,
        stop_event: threading.Event,
        stale_after_s: float = 90.0,
        check_every_s: float = 5.0,
        fail_after_consecutive_errors: int = 3,
    ) -> None:
        super().__init__(name="Heartbeat", daemon=True)
        self.logger = logger
        self.shared_state = shared_state
        self.alerts = alerts
        self.stop_event = stop_event
        self.stale_after_s = stale_after_s
        self.check_every_s = check_every_s
        self.fail_after_consecutive_errors = fail_after_consecutive_errors

    def run(self) -> None:
        self.logger.info("Heartbeat thread started.")
        while not self.stop_event.wait(self.check_every_s):
            snap = self.shared_state.snapshot()
            stale_for = self.shared_state.seconds_since_progress()

            if stale_for > self.stale_after_s and not snap.critical_alert_sent:
                msg = (
                    f"No progress for {stale_for:.1f}s. "
                    f"phase={snap.phase} pending={snap.pending_csv_path}"
                )
                self.shared_state.set_critical_alert_sent()
                self.alerts.critical("SCAN RUNNER HEARTBEAT ALERT", msg)
                self.stop_event.set()
                return

            if snap.consecutive_failures >= self.fail_after_consecutive_errors and not snap.critical_alert_sent:
                msg = (
                    f"Consecutive failures reached {snap.consecutive_failures}. "
                    f"last_error={snap.last_error}"
                )
                self.shared_state.set_critical_alert_sent()
                self.alerts.critical("SCAN RUNNER FAILURE ALERT", msg)
                self.stop_event.set()
                return


# ---------------------------------------------------------------------------
# GUI control layer
# ---------------------------------------------------------------------------

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


class ScanControlManager:
    def __init__(
        self,
        *,
        args: argparse.Namespace,
        cfg: WindowConfig,
        logger: logging.Logger,
    ) -> None:
        self.args = args
        self.cfg = cfg
        self.logger = logger

        script_dir = Path(__file__).resolve().parent
        self.layout_path = args.layout_path or (script_dir / cfg.WIDGET_STACK_YAML)
        self.output_dir = args.output_dir or Path(cfg.MKTBOT_SCANS)

        self.bridge = ManagerBridge()

        self.stop_event: Optional[threading.Event] = None
        self.flags: Optional[SchedulerFlags] = None
        # self.shared_state: Optional[SharedState] = None
        self.shared_state = SharedState(self.args.state_file, logger=self.logger)
        self.alerts: Optional[AlertManager] = None
        self.exporter: Optional[ToSPseudoWidgetExporter] = None
        self.runner: Optional[ScanRunner] = None
        self.heartbeat: Optional[HeartbeatThread] = None
        self.runner_thread: Optional[threading.Thread] = None

    def is_running(self) -> bool:
        return self.runner_thread is not None and self.runner_thread.is_alive()

    def set_gate_active(self, value: bool) -> None:
        if self.flags is not None:
            self.flags.set_gate_active(value)
            self.logger.info("UI | gate_active changed to %s", value)

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

        self.shared_state = SharedState(self.args.state_file)
        self.alerts = AlertManager(self.logger)
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

    # def _run_loop(self) -> None:
    #     try:
    #         assert self.runner is not None
    #         self.runner.run_forever()
    #     except Exception:
    #         self.logger.exception("Fatal error in runner thread.")
    #     finally:
    #         if self.stop_event is not None:
    #             self.stop_event.set()

    #         self.bridge.running_changed.emit(False)
    #         self.bridge.status_changed.emit("Stopped")
    #         self.logger.info("UI | Scan loop stopped.")

    def stop(self) -> None:
        if not self.is_running():
            self.logger.info("UI | Stop ignored because runner is not active.")
            return

        self.logger.info("UI | Stop requested.")
        if self.stop_event is not None:
            self.stop_event.set()
        self.bridge.status_changed.emit("Stopping...")



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

        self.setWindowTitle("Scan Control")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(360, 310)
        self.move(20, 20)

        self._build_ui()

        self.manager.bridge.status_changed.connect(self.status_label.setText)
        self.manager.bridge.running_changed.connect(self.on_running_changed)

        self.on_running_changed(False)
        self.status_label.setText("Ready")
        self._refresh_counters()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(10)

        # ------------------------------------------------------------
        # Title block
        # ------------------------------------------------------------
        title_label = QLabel("Control Panel")
        title_label.setAlignment(Qt.AlignCenter)
        title_font = QFont("Arial Black", 12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        outer.addWidget(title_label)

        file_label = QLabel(Path(__file__).name)
        file_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(file_label)

        # ------------------------------------------------------------
        # Gate checkbox
        # ------------------------------------------------------------
        self.gate_checkbox = QCheckBox("Activate Market Hours Gate")
        self.gate_checkbox.setChecked(True)
        self.gate_checkbox.toggled.connect(self.on_gate_toggled)
        outer.addWidget(self.gate_checkbox)

        # ------------------------------------------------------------
        # Status / counters block
        # ------------------------------------------------------------
        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        outer.addLayout(info_row)

        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        info_row.addLayout(left_col, stretch=0)

        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        info_row.addLayout(right_col, stretch=1)

        self.status_text_label = QLabel("Status:")
        left_col.addWidget(self.status_text_label)

        self.warning_label = QLabel("Warnings:")
        left_col.addWidget(self.warning_label)

        self.error_label = QLabel("Errors:")
        left_col.addWidget(self.error_label)

        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        right_col.addWidget(self.status_label)

        self.warning_value = QLabel("0")
        self.warning_value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        right_col.addWidget(self.warning_value)

        self.error_value = QLabel("0")
        self.error_value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        right_col.addWidget(self.error_value)

        # ------------------------------------------------------------
        # Start / Stop / note row
        # ------------------------------------------------------------
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        outer.addLayout(btn_row)

        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.on_start_clicked)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.on_stop_clicked)
        btn_row.addWidget(self.stop_btn)

        note = QLabel("Esc also requests\ngraceful stop")
        note.setAlignment(Qt.AlignCenter)
        btn_row.addWidget(note)

        # ------------------------------------------------------------
        # Horizontal separator
        # ------------------------------------------------------------
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        outer.addWidget(line)

        # ------------------------------------------------------------
        # Exit button
        # ------------------------------------------------------------
        self.exit_btn = QPushButton("Exit Scan Control")
        self.exit_btn.clicked.connect(self.on_exit_clicked)
        outer.addWidget(self.exit_btn, alignment=Qt.AlignCenter)

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
        self.gate_checkbox.setEnabled(False)
        self.status_label.setText("Stopping before exit...")

    @Slot(int)
    def on_log_level_seen(self, levelno: int) -> None:
        if levelno >= logging.ERROR:
            self.error_count += 1
        elif levelno >= logging.WARNING:
            self.warning_count += 1

        self._refresh_counters()

    @Slot(bool)
    def on_gate_toggled(self, checked: bool) -> None:
        self.logger.info("UI | Gate checkbox changed to %s", checked)
        self.manager.set_gate_active(checked)

    @Slot()
    def on_start_clicked(self) -> None:
        try:
            self.manager.start(gate_active=self.gate_checkbox.isChecked())
        except StartupValidationError as exc:
            self.status_label.setText("Start failed")
            QMessageBox.critical(self, "Startup Validation Error", str(exc))
        except Exception as exc:
            self.status_label.setText("Start failed")
            self.logger.exception("Unexpected error during start: %s", exc)
            QMessageBox.critical(self, "Unexpected Error", str(exc))

    @Slot()
    def on_stop_clicked(self) -> None:
        self.manager.stop()

    @Slot()
    def on_escape_pressed(self) -> None:
        if self.manager.is_running():
            self.logger.info("ESC | Stop requested by Escape key.")
            self.manager.stop()

    @Slot()
    def on_exit_clicked(self) -> None:
        self.logger.info("UI | Exit Scan Control requested.")

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
        self.start_btn.setEnabled(not running and not self.exit_requested)
        self.stop_btn.setEnabled(running and not self.exit_requested)
        self.gate_checkbox.setEnabled(not self.exit_requested)

        if self.exit_requested and not running:
            self.logger.info("UI | Runner stopped; exiting application.")
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return

    def closeEvent(self, event) -> None:
        """
        Window-manager close behaves like Exit Scan Control:
        request graceful stop first, then quit after runner stops.
        """
        if self.manager.is_running() and not self.exit_requested:
            self.logger.info("UI | Window close requested while runner active.")
            self.exit_requested = True
            self.manager.stop()
            self._set_exit_pending_ui()
            event.ignore()
            return

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



# class ScanControlPanel(QWidget):
#     def __init__(
#     self,
#     *,
#     manager: ScanControlManager,
#     logger: logging.Logger,
# ) -> None:
#         super().__init__()
#         self.manager = manager
#         self.logger = logger

#         self.warning_count = 0
#         self.error_count = 0

#         self.gui_log_bridge = GuiLogBridge()
#         self.gui_log_bridge.log_level_seen.connect(self.on_log_level_seen)

#         self.qt_counter_handler = QtCounterLogHandler(self.gui_log_bridge)
#         self.logger.addHandler(self.qt_counter_handler)

#         self.escape_bridge = EscapeBridge()
#         self.escape_bridge.escape_pressed.connect(self.on_escape_pressed)
#         self.escape_listener = keyboard.Listener(on_press=self._on_key_press)
#         self.escape_listener.start()

#         self.setWindowTitle("Scan Control")
#         self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
#         self.resize(360, 170)
#         self.move(20, 20)

#         self._build_ui()

#         self.manager.bridge.status_changed.connect(self.status_label.setText)
#         self.manager.bridge.running_changed.connect(self.on_running_changed)

#         self.on_running_changed(False)
#         self.status_label.setText("Ready")
#         self._refresh_counters()
    
    
#     # def __init__(
#     #     self,
#     #     *,
#     #     manager: ScanControlManager,
#     #     logger: logging.Logger,
#     # ) -> None:
#     #     super().__init__()
#     #     self.manager = manager
#     #     self.logger = logger

#     #     self.escape_bridge = EscapeBridge()
#     #     self.escape_bridge.escape_pressed.connect(self.on_escape_pressed)
#     #     self.escape_listener = keyboard.Listener(on_press=self._on_key_press)
#     #     self.escape_listener.start()

#     #     self.setWindowTitle("Scan Control")
#     #     self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
#     #     self.resize(320, 130)
#     #     self.move(20, 20)

#     #     self._build_ui()

#     #     self.manager.bridge.status_changed.connect(self.status_label.setText)
#     #     self.manager.bridge.running_changed.connect(self.on_running_changed)

#     #     self.on_running_changed(False)
#     #     self.status_label.setText("Ready")


#     def _build_ui(self) -> None:
#         outer = QVBoxLayout(self)

#         title = QLabel("scan_main.py Control Panel")
#         title.setAlignment(Qt.AlignCenter)
#         title.setStyleSheet("font-weight: bold;")
#         outer.addWidget(title)

#         self.gate_checkbox = QCheckBox("Activate Market Hours Gate")
#         self.gate_checkbox.setChecked(True)
#         self.gate_checkbox.toggled.connect(self.on_gate_toggled)
#         outer.addWidget(self.gate_checkbox)

#         self.status_label = QLabel("Ready")
#         self.status_label.setAlignment(Qt.AlignCenter)
#         outer.addWidget(self.status_label)

#         counts_row = QHBoxLayout()
#         outer.addLayout(counts_row)

#         self.warning_label = QLabel("Warnings:")
#         counts_row.addWidget(self.warning_label)

#         self.warning_value = QLabel("0")
#         self.warning_value.setAlignment(Qt.AlignCenter)
#         self.warning_value.setStyleSheet("min-width: 40px; font-weight: bold;")
#         counts_row.addWidget(self.warning_value)

#         self.error_label = QLabel("Errors:")
#         counts_row.addWidget(self.error_label)

#         self.error_value = QLabel("0")
#         self.error_value.setAlignment(Qt.AlignCenter)
#         self.error_value.setStyleSheet("min-width: 40px; font-weight: bold;")
#         counts_row.addWidget(self.error_value)

#         btn_row = QHBoxLayout()
#         outer.addLayout(btn_row)

#         self.start_btn = QPushButton("Start")
#         self.start_btn.clicked.connect(self.on_start_clicked)
#         btn_row.addWidget(self.start_btn)

#         self.stop_btn = QPushButton("Stop")
#         self.stop_btn.clicked.connect(self.on_stop_clicked)
#         btn_row.addWidget(self.stop_btn)

#         note = QLabel("Esc also requests graceful stop")
#         note.setAlignment(Qt.AlignCenter)
#         outer.addWidget(note)



#     # def _build_ui(self) -> None:
#     #     outer = QVBoxLayout(self)

#     #     title = QLabel("scan_main.py Control Panel")
#     #     title.setAlignment(Qt.AlignCenter)
#     #     title.setStyleSheet("font-weight: bold;")
#     #     outer.addWidget(title)

#     #     self.gate_checkbox = QCheckBox("Activate Market Hours Gate")
#     #     self.gate_checkbox.setChecked(True)
#     #     self.gate_checkbox.toggled.connect(self.on_gate_toggled)
#     #     outer.addWidget(self.gate_checkbox)

#     #     self.status_label = QLabel("Ready")
#     #     self.status_label.setAlignment(Qt.AlignCenter)
#     #     outer.addWidget(self.status_label)

#     #     btn_row = QHBoxLayout()
#     #     outer.addLayout(btn_row)

#     #     self.start_btn = QPushButton("Start")
#     #     self.start_btn.clicked.connect(self.on_start_clicked)
#     #     btn_row.addWidget(self.start_btn)

#     #     self.stop_btn = QPushButton("Stop")
#     #     self.stop_btn.clicked.connect(self.on_stop_clicked)
#     #     btn_row.addWidget(self.stop_btn)

#     #     note = QLabel("Esc also requests graceful stop")
#     #     note.setAlignment(Qt.AlignCenter)
#     #     outer.addWidget(note)


#     def _refresh_counters(self) -> None:
#         self.warning_value.setText(str(self.warning_count))
#         self.error_value.setText(str(self.error_count))


#     @Slot(int)
#     def on_log_level_seen(self, levelno: int) -> None:
#         if levelno >= logging.ERROR:
#             self.error_count += 1
#         elif levelno >= logging.WARNING:
#             self.warning_count += 1

#         self._refresh_counters()


#     def _on_key_press(self, key) -> None:
#         if key == keyboard.Key.esc:
#             self.escape_bridge.escape_pressed.emit()


#     def closeEvent(self, event) -> None:
#         try:
#             self.manager.stop()
#             self.escape_listener.stop()
#         except Exception:
#             pass

#         try:
#             self.logger.removeHandler(self.qt_counter_handler)
#             self.qt_counter_handler.close()
#         except Exception:
#             pass

#         super().closeEvent(event)


#     # def closeEvent(self, event) -> None:
#     #     try:
#     #         self.manager.stop()
#     #         self.escape_listener.stop()
#     #     except Exception:
#     #         pass
#     #     super().closeEvent(event)


#     @Slot(bool)
#     def on_gate_toggled(self, checked: bool) -> None:
#         self.logger.info("UI | Gate checkbox changed to %s", checked)
#         self.manager.set_gate_active(checked)

#     @Slot()
#     def on_start_clicked(self) -> None:
#         try:
#             self.manager.start(gate_active=self.gate_checkbox.isChecked())
#         except StartupValidationError as exc:
#             self.status_label.setText("Start failed")
#             QMessageBox.critical(self, "Startup Validation Error", str(exc))
#         except Exception as exc:
#             self.status_label.setText("Start failed")
#             self.logger.exception("Unexpected error during start: %s", exc)
#             QMessageBox.critical(self, "Unexpected Error", str(exc))

#     @Slot()
#     def on_stop_clicked(self) -> None:
#         self.manager.stop()

#     @Slot()
#     def on_escape_pressed(self) -> None:
#         if self.manager.is_running():
#             self.logger.info("ESC | Stop requested by Escape key.")
#             self.manager.stop()

#     @Slot(bool)
#     def on_running_changed(self, running: bool) -> None:
#         self.start_btn.setEnabled(not running)
#         self.stop_btn.setEnabled(running)


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
        help="Directory for CSV files; defaults to WindowConfig.MKTBOT_SCANS",
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
        help="Optional layout file for pseudo-widget definitions; defaults to WindowConfig.WIDGET_STACK_YAML",
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
    cfg = WindowConfig()

    logger, listener = build_logger(args.log_dir)
    listener.start()

    app = QApplication([])

    try:
        logger.info("========================================================")
        logger.info("scan_main.py startup")
        logger.info("output_dir=%s", args.output_dir or Path(cfg.MKTBOT_SCANS))
        logger.info("log_dir=%s", args.log_dir)
        logger.info("state_file=%s", args.state_file)
        logger.info("layout_path=%s", args.layout_path or Path(cfg.WIDGET_STACK_YAML))
        logger.info("dry_run=%s", args.dry_run)
        logger.info("WINDOW_TOS_MAIN prefix=%s", cfg.WINDOW_TOS_MAIN)

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

    