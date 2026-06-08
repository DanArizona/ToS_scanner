# scan_control_state.py

"""Thread-safe control state for scan execution.

This module contains small coordination helpers for pause state, user-requested
scans, and dynamic waits that can be interrupted by stop, pause, or scheduling
state changes.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from scheduler import SchedulerFlags


ET = ZoneInfo("America/New_York")


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
