# run_state.py

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


UTC = ZoneInfo("UTC")
ET = ZoneInfo("America/New_York")


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
                f"{self.state_file.stem}."
                f"{os.getpid()}."
                f"{threading.get_ident()}."
                f"{uuid.uuid4().hex}.tmp"
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
