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
from typing import Callable, Optional, Protocol
from zoneinfo import ZoneInfo

import pygetwindow as gw
from pynput import keyboard

from PySide6.QtCore import QObject, Signal, Slot, Qt, QTimer
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

from config import ScannerConfig, load_scanner_config
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
USER_SCAN_MIN_LEAD_S = 7.0

DEFAULT_STATE_FILE = Path("./runtime/scan_runner_state.json")
PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"

REQUIRED_ENV_VARS = []

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
    # missing: list[str] = []
    # for name in REQUIRED_ENV_VARS:
    #     value = os.environ.get(name)
    #     if value is None or not value.strip():
    #         missing.append(name)

    # if missing:
    #     raise StartupValidationError(
    #         "Required environment variable(s) not set: " + ", ".join(missing)
    #     )

    # logger.info("Startup check passed: required Pushover environment variables are set.")

    logger.info("Startup check passed: no required plain environment secrets.")

def get_matching_window(title_prefix: str):
    normalized_prefix = title_prefix.strip()

    for win in gw.getAllWindows():
        title = (win.title or "").strip()
        if title.startswith(normalized_prefix):
            return win

    return None


def validate_win_main_open(logger: logging.Logger, cfg: ScannerConfig):
    title_prefix = cfg.window_tos_main
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
    cfg: ScannerConfig,
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
    cfg: ScannerConfig,
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
        layout = load_widget_layout(layout_path, cfg.title_map)
    except Exception as exc:
        raise StartupValidationError(
            f"Could not load layout YAML '{layout_path}': {exc}"
        ) from exc

    validate_win_main_size(logger, cfg, layout, win_main_window)
    validate_output_dir_access(logger, output_dir)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PushoverCredentials:
    app_token: str
    user_key: str


def load_pushover_credentials(cfg: ScannerConfig) -> Optional[PushoverCredentials]:
    """
    Temporary placeholder.

    Later this should read cfg.pushover_ecfg_path using mb_tools.secure_config.
    For now, returning None means credentials are not configured.
    """
    return None


class AlertManager:
    def __init__(
        self,
        logger: logging.Logger,
        *,
        notifications_enabled: bool = False,
        pushover_credentials: Optional[PushoverCredentials] = None,
    ) -> None:
        self.logger = logger
        self.notifications_enabled = notifications_enabled
        self.pushover_credentials = pushover_credentials

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
        if not self.notifications_enabled:
            self.logger.info("Pushover skipped: notifications are disabled.")
            return

        if self.pushover_credentials is None:
            self.logger.error(
                "Pushover requested but credentials are not loaded."
            )
            return

        payload = urllib.parse.urlencode(
            {
                "token": self.pushover_credentials.app_token,
                "user": self.pushover_credentials.user_key,
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

def wait_while_paused(
    stop_event: threading.Event,
    pause_ctl: PauseController,
) -> bool:
    while pause_ctl.is_paused():
        if stop_event.is_set():
            return False
        time.sleep(0.1)
    return True

class UserScanRequest:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending = False
        self._active = False

    def start_immediate(self) -> bool:
        with self._lock:
            if self._pending or self._active:
                return False
            self._active = True
            return True

    def request_deferred(self) -> bool:
        with self._lock:
            if self._pending or self._active:
                return False
            self._pending = True
            return True

    def consume_pending(self) -> bool:
        with self._lock:
            if not self._pending or self._active:
                return False
            self._pending = False
            self._active = True
            return True

    def finish(self) -> None:
        with self._lock:
            self._active = False

    def snapshot(self) -> tuple[bool, bool]:
        with self._lock:
            return self._pending, self._active
        
class PauseController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused = False
        self._generation = 0

    def snapshot(self) -> tuple[bool, int]:
        with self._lock:
            return self._paused, self._generation

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def set_paused(self, value: bool) -> None:
        with self._lock:
            if self._paused != value:
                self._paused = value
                self._generation += 1
                

def wait_until_dynamic(
    target: datetime,
    stop_event: threading.Event,
    flags: SchedulerFlags,
    expected_generation: int,
    pause_ctl: PauseController,
    expected_pause_generation: int,
) -> str:
    """
    Returns:
      - "fired"
      - "stopped"
      - "recompute"
    """
    while not stop_event.is_set():
        _, generation = flags.snapshot()
        paused, pause_generation = pause_ctl.snapshot()

        if generation != expected_generation or pause_generation != expected_pause_generation:
            return "recompute"

        if paused:
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

# class ScanExporter(Protocol):
#     def export_scan(
#         self,
#         csv_path: Path,
#         slot_et: datetime,
#         stop_event: Optional[threading.Event] = None,
#     ) -> None:
#         ...

class ScanExporter(Protocol):
    def export_scan(
        self,
        csv_path: Path,
        slot_et: datetime,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        ...

    def perform_user_scan(
        self,
        *,
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

    Also provides perform_user_scan() for the UI Scan button.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger,
        cfg: ScannerConfig,
        layout_path: Optional[Path] = None,
        dry_run: bool = False,
        verify_timeout_s: float = 10.0,
    ) -> None:
        self.logger = logger
        self.cfg = cfg
        self.dry_run = dry_run
        self.verify_timeout_s = verify_timeout_s

        self.controller = ToSDebugController(
            layout_path=layout_path or cfg.pwidget_yaml_path,
            cfg=cfg,
            logger=logger,
        )

        # Normal operating timing/jitter
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

        # Hold the controller lock for the full export sequence so that
        # maintenance or user-scan actions cannot interleave with it.
        with self.controller.action_lock:
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

    def perform_user_scan(
        self,
        *,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        if self.dry_run:
            self.logger.info("DRY RUN | user_scan skipped")
            return

        self._check_stop(stop_event)
        self.controller.user_scan(pre_wait_s=1.0)



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
        pause_ctl: PauseController,
        user_scan_request: UserScanRequest,
    ) -> None:
        self.exporter = exporter
        self.logger = logger
        self.shared_state = shared_state
        self.stop_event = stop_event
        self.output_dir = output_dir
        self.flags = flags
        self.pause_ctl = pause_ctl
        self.user_scan_request = user_scan_request

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run_forever(self) -> None:
        self.logger.info("Scan runner entering main loop.")
        self.shared_state.touch(phase="idle")

        while not self.stop_event.is_set():
            if self.pause_ctl.is_paused():
                self.logger.info("Runner paused/deflected; waiting for maintenance action to complete.")
                self.shared_state.touch(phase="paused")
                if not wait_while_paused(self.stop_event, self.pause_ctl):
                    self.logger.info("Stop requested while paused.")
                    self.shared_state.clear_pending(phase="stopping")
                    return
                self.shared_state.touch(phase="idle")
                continue

            now_et = datetime.now(ET)
            gate_active, generation = self.flags.snapshot()
            paused, pause_generation = self.pause_ctl.snapshot()

            slot_et = next_slot_after(now_et, gate_active)
            csv_path = self.output_dir / f"scan-{slot_et:%Y-%m-%d-%H-%M-%S}-ToS.csv"

            self.logger.info(
                "Next slot=%s gate_active=%s paused=%s target_csv=%s",
                slot_et.isoformat(),
                gate_active,
                paused,
                csv_path,
            )

            self.shared_state.set_pending(slot_et, csv_path)
            self.shared_state.touch(phase="waiting_for_slot")

            wait_result = wait_until_dynamic(
                slot_et,
                self.stop_event,
                self.flags,
                generation,
                self.pause_ctl,
                pause_generation,
            )

            if wait_result == "stopped":
                self.logger.info("Stop requested before next slot fired.")
                self.shared_state.clear_pending(phase="stopping")
                return

            if wait_result == "recompute":
                self.logger.info("Scheduler state changed; recomputing next slot.")
                self.shared_state.clear_pending(phase="idle")
                continue

            if self.pause_ctl.is_paused():
                self.logger.info("Pause requested at slot boundary; deflecting export.")
                self.shared_state.clear_pending(phase="paused")
                continue

            try:
                self.shared_state.touch(phase="executing_gui")
                self.exporter.export_scan(csv_path, slot_et, stop_event=self.stop_event)

                self.shared_state.mark_completed(slot_et)

                # If a user-requested scan was deferred because there was not
                # enough time before this export, run it now.
                if self.user_scan_request.consume_pending():
                    self.logger.info("Runner servicing deferred user scan after export.")
                    try:
                        self.shared_state.touch(phase="manual_scan")
                        self.exporter.perform_user_scan(stop_event=self.stop_event)
                    except Exception as exc:
                        self.logger.exception("Deferred user scan failed: %s", exc)
                    finally:
                        self.user_scan_request.finish()

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

        # self.alerts = AlertManager(self.logger)
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
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
        else:
            self.radio_production.setEnabled(True)
            self.radio_debug.setEnabled(True)
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

    