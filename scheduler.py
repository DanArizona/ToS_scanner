# scheduler.py

"""Scheduling helpers for timed ToS scan exports.

This module owns market-time scheduling constants, gate-aware slot calculation,
and wait helpers used by the scan runner.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
SLOT_SECONDS = (5, 20, 35, 50)
MARKET_OPEN = dt_time(9, 24, 0)
MARKET_CLOSE = dt_time(16, 2, 0)


class SchedulerFlags:
    def __init__(self, *, gate_active: bool = True) -> None:
        self._lock = threading.Lock()
        self._gate_active = gate_active        
        self._generation = 0

    def snapshot(self) -> tuple[bool, int]:
        with self._lock:
            return self._gate_active, self._generation

    def set_gate_active(self, value: bool) -> None:
        with self._lock:
            if self._gate_active != value:
                self._gate_active = value
                self._generation += 1


def is_weekday(dt_et: datetime) -> bool:
    return dt_et.weekday() < 5


def next_slot_after(now_et: datetime, gate_active: bool) -> datetime:
    """
    Return the next scheduled slot after now_et.

    If gate_active is True, slots are constrained to weekday market-window time.
    If gate_active is False, slots are based only on SLOT_SECONDS.
    """
    candidate = now_et.replace(microsecond=0) + timedelta(seconds=1)

    while True:
        if candidate.second in SLOT_SECONDS:
            if not gate_active:
                return candidate

            if is_weekday(candidate) and MARKET_OPEN <= candidate.time() < MARKET_CLOSE:
                return candidate

        candidate += timedelta(seconds=1)

        if gate_active:
            if is_weekday(candidate) and candidate.time() < MARKET_OPEN:
                candidate = candidate.replace(
                    hour=MARKET_OPEN.hour,
                    minute=MARKET_OPEN.minute,
                    second=0,
                    microsecond=0,
                )

            if (not is_weekday(candidate)) or candidate.time() >= MARKET_CLOSE:
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


def sleep_until_or_stop(
    target: datetime,
    stop_event: threading.Event,
    *,
    check_interval: float = 0.2,
) -> bool:
    """
    Sleep until target ET time or until stop_event is set.

    Returns True if the target time was reached.
    Returns False if stop_event was set first.
    """
    while not stop_event.is_set():
        remaining = (target - datetime.now(ET)).total_seconds()

        if remaining <= 0:
            return True

        time.sleep(min(check_interval, remaining))

    return False
