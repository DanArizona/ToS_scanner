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

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Optional, Protocol
from zoneinfo import ZoneInfo

import pygetwindow as gw

from config import WindowConfig
from layout import load_widget_layout


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# ACTIVE_GATE = True
ACTIVE_GATE = False

SLOT_SECONDS = (5, 20, 35, 50)
MARKET_OPEN = dt_time(9, 30, 0)
MARKET_CLOSE = dt_time(16, 0, 0)

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
    def __init__(self, state_file: Path) -> None:
        self._lock = threading.Lock()
        self.state_file = state_file
        self.model = PersistentRunState()
        self.last_progress_monotonic = time.monotonic()
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
                last_error="Could not parse existing state file."
            )

    def save(self) -> None:
        with self._lock:
            self.model.last_progress_utc = datetime.now(UTC).isoformat()
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(asdict(self.model), indent=2),
                encoding="utf-8"
            )
            tmp.replace(self.state_file)

    def touch(self, *, phase: Optional[str] = None) -> None:
        with self._lock:
            if phase is not None:
                self.model.phase = phase
            self.last_progress_monotonic = time.monotonic()
        self.save()

    def set_pending(self, slot_et: datetime, csv_path: Path) -> None:
        with self._lock:
            self.model.phase = "pending_slot"
            self.model.pending_slot_et = slot_et.isoformat()
            self.model.pending_csv_path = str(csv_path)
            self.model.last_error = None
            self.last_progress_monotonic = time.monotonic()
        self.save()

    def mark_completed(self, slot_et: datetime) -> None:
        with self._lock:
            self.model.phase = "completed_slot"
            self.model.last_completed_slot_et = slot_et.isoformat()
            self.model.pending_slot_et = None
            self.model.pending_csv_path = None
            self.model.last_error = None
            self.model.consecutive_failures = 0
            self.last_progress_monotonic = time.monotonic()
        self.save()

    def mark_failure(self, message: str) -> None:
        with self._lock:
            self.model.phase = "error"
            self.model.last_error = message
            self.model.consecutive_failures += 1
            self.last_progress_monotonic = time.monotonic()
        self.save()

    def clear_pending_as_missed(self, message: str) -> None:
        with self._lock:
            self.model.phase = "missed_slot"
            self.model.last_error = message
            self.model.pending_slot_et = None
            self.model.pending_csv_path = None
            self.last_progress_monotonic = time.monotonic()
        self.save()

    def snapshot(self) -> PersistentRunState:
        with self._lock:
            return PersistentRunState(**asdict(self.model))

    def seconds_since_progress(self) -> float:
        with self._lock:
            return time.monotonic() - self.last_progress_monotonic

    def set_critical_alert_sent(self) -> None:
        with self._lock:
            self.model.critical_alert_sent = True
        self.save()


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
        datefmt="%Y-%m-%d %H:%M:%S"
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
        respect_handler_level=True
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
    """
    Return the first window whose title starts with title_prefix.
    This is more robust than exact matching when Schwab appends build numbers.
    Example:
        Main@thinkorswim [build 1990]
    """
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


def validate_output_dir_access(logger: logging.Logger, output_dir: Path) -> None:
    """
    Ensure the output directory exists and is writable.
    For an existing UNC share, this also verifies current SMB access.
    """
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


def extract_expected_size_from_layout(layout, widget_name: str) -> tuple[int, int]:
    """
    Tries a few likely layout shapes so scan_main.py stays decoupled from
    exact internal object names.

    Adjust this function only if your actual layout object uses different names.
    """
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
        expected_w, expected_h, actual_w, actual_h, tolerance_px
    )


def run_startup_checks(
    logger: logging.Logger,
    cfg: WindowConfig,
    layout_path: Optional[Path],
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
    validate_output_dir_access(logger, Path(cfg.MKTBOT_SCANS))

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
                0x10 | 0x1000  # MB_ICONHAND | MB_SYSTEMMODAL
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
# Scheduling helpers
# ---------------------------------------------------------------------------

def is_weekday(dt_et: datetime) -> bool:
    return dt_et.weekday() < 5


def is_market_session(dt_et: datetime) -> bool:
    if not ACTIVE_GATE:
        return True

    if not is_weekday(dt_et):
        return False

    t = dt_et.timetz().replace(tzinfo=None)
    return MARKET_OPEN <= t < MARKET_CLOSE


# def is_market_session(dt_et: datetime) -> bool:
#     if not is_weekday(dt_et):
#         return False
#     t = dt_et.timetz().replace(tzinfo=None)
#     return MARKET_OPEN <= t < MARKET_CLOSE


def next_slot_after(now_et: datetime) -> datetime:
    """
    Return the next slot at :05, :20, :35, :50.

    If ACTIVE_GATE is True:
        restrict to weekday core market session.
    If ACTIVE_GATE is False:
        run continuously on the slot cadence, regardless of market hours.
    """
    candidate = now_et.replace(microsecond=0) + timedelta(seconds=1)

    if not ACTIVE_GATE:
        while candidate.second not in SLOT_SECONDS:
            candidate += timedelta(seconds=1)
        return candidate

    while True:
        if is_weekday(candidate) and candidate.timetz().replace(tzinfo=None) < MARKET_OPEN:
            candidate = candidate.replace(
                hour=MARKET_OPEN.hour,
                minute=MARKET_OPEN.minute,
                second=0,
                microsecond=0
            )

        if (not is_weekday(candidate)) or candidate.timetz().replace(tzinfo=None) >= MARKET_CLOSE:
            candidate = (candidate + timedelta(days=1)).replace(
                hour=MARKET_OPEN.hour,
                minute=MARKET_OPEN.minute,
                second=0,
                microsecond=0
            )
            while not is_weekday(candidate):
                candidate = (candidate + timedelta(days=1)).replace(
                    hour=MARKET_OPEN.hour,
                    minute=MARKET_OPEN.minute,
                    second=0,
                    microsecond=0
                )
            continue

        if candidate.second in SLOT_SECONDS and is_market_session(candidate):
            return candidate

        candidate += timedelta(seconds=1)



# def next_slot_after(now_et: datetime) -> datetime:
#     """
#     Return the next slot at :05, :20, :35, :50 within weekday core session.
#     Starter version: weekdays only. Holiday suppression can be added later.
#     """
#     candidate = now_et.replace(microsecond=0) + timedelta(seconds=1)

#     while True:
#         if is_weekday(candidate) and candidate.timetz().replace(tzinfo=None) < MARKET_OPEN:
#             candidate = candidate.replace(
#                 hour=MARKET_OPEN.hour,
#                 minute=MARKET_OPEN.minute,
#                 second=0,
#                 microsecond=0
#             )

#         if (not is_weekday(candidate)) or candidate.timetz().replace(tzinfo=None) >= MARKET_CLOSE:
#             candidate = (candidate + timedelta(days=1)).replace(
#                 hour=MARKET_OPEN.hour,
#                 minute=MARKET_OPEN.minute,
#                 second=0,
#                 microsecond=0
#             )
#             while not is_weekday(candidate):
#                 candidate = (candidate + timedelta(days=1)).replace(
#                     hour=MARKET_OPEN.hour,
#                     minute=MARKET_OPEN.minute,
#                     second=0,
#                     microsecond=0
#                 )
#             continue

#         if candidate.second in SLOT_SECONDS and is_market_session(candidate):
#             return candidate

#         candidate += timedelta(seconds=1)


def slot_filename(slot_et: datetime) -> str:
    return f"scan-{slot_et:%Y-%m-%d-%H-%M-%S}-ToS.csv"


def wait_until(target: datetime, stop_event: threading.Event) -> bool:
    while not stop_event.is_set():
        remaining = (target - datetime.now(ET)).total_seconds()
        if remaining <= 0:
            return True
        time.sleep(min(remaining, 0.25))
    return False


def wait_for_file(path: Path, timeout_s: float, stop_event: threading.Event) -> bool:
    deadline = time.monotonic() + timeout_s
    while not stop_event.is_set() and time.monotonic() < deadline:
        if path.exists() and path.stat().st_size >= 0:
            return True
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# ToS pseudo-widget adapter
# ---------------------------------------------------------------------------

class ScanExporter(Protocol):
    def export_scan(self, csv_path: Path, slot_et: datetime) -> None:
        ...


class ToSPseudoWidgetExporter:
    """
    Thin adapter over your existing pseudo-widget/window machinery.

    Replace the TODO block inside export_scan() with the exact sequence later:
      1. bring ToS to front
      2. navigate to the proper scanner/export UI
      3. enter csv_path.name or full path as required
      4. confirm save
    """

    def __init__(
        self,
        logger: logging.Logger,
        layout_path: Optional[Path] = None,
        dry_run: bool = False
    ) -> None:
        self.logger = logger
        self.cfg = WindowConfig()
        # self.layout = load_widget_layout(layout_path) if layout_path else None
        self.layout = load_widget_layout(layout_path, self.cfg.TITLE_MAP) if layout_path else None
        self.dry_run = dry_run

    def ensure_tos_main_window(self) -> None:
        title_prefix = self.cfg.WINDOW_TOS_MAIN
        win = get_matching_window(title_prefix)

        if win is None:
            raise RuntimeError(f"ToS main window is not visible: prefix={title_prefix!r}")

        try:
            if hasattr(win, "isMinimized") and win.isMinimized:
                win.restore()
        except Exception:
            pass

        try:
            win.activate()
        except Exception:
            pass

        self.logger.info("GUI | ToS main window brought to front: %s", win.title)
        time.sleep(0.25)

    def export_scan(self, csv_path: Path, slot_et: datetime) -> None:
        self.ensure_tos_main_window()
        self.logger.info("GUI | Begin export for slot %s", slot_et.isoformat())
        self.logger.info("GUI | Target CSV filename: %s", csv_path.name)

        if self.dry_run:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_text(
                "Symbol,%Change,Volume,Last\nTEST,+0.00%,1000,1.23\n",
                encoding="utf-8"
            )
            self.logger.info("DRY RUN | Stub CSV created at %s", csv_path)
            return

        # -------------------------------------------------------------------
        # TODO:
        # Call your pseudo-widget interaction functions here.
        #
        # Example future shape:
        #   save_scan_via_widgets(
        #       widget_layout=self.layout,
        #       output_path=csv_path,
        #       logger=self.logger,
        #   )
        # -------------------------------------------------------------------

        raise NotImplementedError(
            "Pseudo-widget export sequence has not been implemented yet."
        )


# ---------------------------------------------------------------------------
# Heartbeat monitor
# ---------------------------------------------------------------------------

class HeartbeatThread(threading.Thread):
    def __init__(
        self,
        *,
        logger: logging.Logger,
        shared_state: SharedState,
        alerts: AlertManager,
        stop_event: threading.Event,
        stale_after_s: float = 45.0,
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
        verify_timeout_s: float = 10.0,
    ) -> None:
        self.exporter = exporter
        self.logger = logger
        self.shared_state = shared_state
        self.stop_event = stop_event
        self.output_dir = output_dir
        self.verify_timeout_s = verify_timeout_s

        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run_forever(self) -> None:
        self.logger.info("Scan runner entering main loop.")
        self.shared_state.touch(phase="idle")

        while not self.stop_event.is_set():
            now_et = datetime.now(ET)
            slot_et = next_slot_after(now_et)
            csv_path = self.output_dir / slot_filename(slot_et)

            self.logger.info(
                "Next slot=%s target_csv=%s",
                slot_et.isoformat(),
                csv_path,
            )

            self.shared_state.set_pending(slot_et, csv_path)
            self.shared_state.touch(phase="waiting_for_slot")

            fired = wait_until(slot_et, self.stop_event)
            if not fired:
                self.logger.info("Stop requested before next slot fired.")
                return

            try:
                self.shared_state.touch(phase="executing_gui")
                self.exporter.export_scan(csv_path, slot_et)

                self.shared_state.touch(phase="verifying_csv")
                ok = wait_for_file(csv_path, self.verify_timeout_s, self.stop_event)
                if not ok:
                    raise TimeoutError(
                        f"CSV file was not detected within {self.verify_timeout_s}s: {csv_path}"
                    )

                self.logger.info("CSV output verified: %s", csv_path)
                self.shared_state.mark_completed(slot_et)
                self.shared_state.touch(phase="idle")

            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                self.logger.exception("Slot execution failed: %s", msg)
                self.shared_state.mark_failure(msg)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run timed ToS scan exports at :05, :20, :35, :50 of each minute."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for CSV files; defaults to WindowConfig.MKTBOT_SCANS"          
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("./logs"),
        help="Directory for daily log files"
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"Path to persistent runtime state json (default: {DEFAULT_STATE_FILE})"
    )
    parser.add_argument(
        "--layout-path",
        type=Path,
        default=None,
        help="Optional layout file for pseudo-widget definitions; defaults to WindowConfig.WIDGET_STACK_YAML"
    )
    parser.add_argument(
        "--verify-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for CSV file to appear after GUI export"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create stub CSVs instead of interacting with ToS"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    cfg = WindowConfig()
    script_dir = Path(__file__).resolve().parent

    layout_path = args.layout_path or (script_dir / cfg.WIDGET_STACK_YAML)
    output_dir = args.output_dir or Path(cfg.MKTBOT_SCANS)
    log_dir = args.log_dir

    logger, listener = build_logger(log_dir)
    listener.start()

    stop_event = threading.Event()
    shared_state = SharedState(args.state_file)
    alerts = AlertManager(logger)

    try:
        logger.info("========================================================")
        logger.info("Scan runner startup")
        logger.info("ACTIVE_GATE=%s", ACTIVE_GATE)
        logger.info("output_dir=%s", output_dir)
        logger.info("log_dir=%s", log_dir)
        logger.info("state_file=%s", args.state_file)
        logger.info("layout_path=%s", layout_path)
        logger.info("dry_run=%s", args.dry_run)
        logger.info("WINDOW_TOS_MAIN prefix=%s", cfg.WINDOW_TOS_MAIN)

        run_startup_checks(
            logger=logger,
            cfg=cfg,
            layout_path=layout_path,
        )

        recover_previous_run(logger, shared_state)

        exporter = ToSPseudoWidgetExporter(
            logger=logger,
            layout_path=layout_path,
            dry_run=args.dry_run,
        )

        heartbeat = HeartbeatThread(
            logger=logger,
            shared_state=shared_state,
            alerts=alerts,
            stop_event=stop_event,
            stale_after_s=45.0,
            check_every_s=5.0,
            fail_after_consecutive_errors=3,
        )
        heartbeat.start()

        runner = ScanRunner(
            exporter=exporter,
            logger=logger,
            shared_state=shared_state,
            stop_event=stop_event,
            output_dir=output_dir,
            verify_timeout_s=args.verify_timeout,
        )

        runner.run_forever()
        logger.info("Scan runner exited normally.")
        return 0

    except StartupValidationError as exc:
        return fatal_startup(logger, str(exc), exit_code=2)

    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt received. Stopping gracefully.")
        stop_event.set()
        shared_state.touch(phase="stopping")
        return 130

    except Exception as exc:
        logger.exception("Fatal startup/runtime error: %s", exc)
        stop_event.set()
        shared_state.mark_failure(f"Fatal error: {exc}")
        alerts.critical("SCAN RUNNER FATAL ERROR", str(exc))
        return 1

    finally:
        listener.stop()


if __name__ == "__main__":
    raise SystemExit(main())
