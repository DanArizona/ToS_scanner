# scanner_heartbeat.py

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scan_jobs import JobRequest, JobResult


def utc_now_text() -> str:
    """
    Return the current UTC time in a compact ISO-8601 form.
    """
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


@dataclass
class ScannerHeartbeatPublisher:
    """
    Publish scanner status atomically under command_root/status/.

    Normal calls are rate-limited by interval_s. Pass force=True when an
    important state transition should be visible immediately.
    """

    command_root: Path
    interval_s: float = 5.0
    application_name: str = "ToS_scanner"
    status_directory_name: str = "status"
    heartbeat_filename: str = "scanner_heartbeat.json"

    started_at_utc: str = field(default_factory=utc_now_text)

    _sequence: int = field(default=0, init=False)
    _last_publish_monotonic: float | None = field(
        default=None,
        init=False,
    )

    def __post_init__(self) -> None:
        self.command_root = Path(self.command_root)

        if self.interval_s <= 0:
            raise ValueError("interval_s must be greater than zero.")

        self.status_dir = (
            self.command_root / self.status_directory_name
        )
        self.heartbeat_path = (
            self.status_dir / self.heartbeat_filename
        )

    def publish(
        self,
        *,
        running: bool,
        paused: bool,
        shutdown_requested: bool,
        loop_state: str,
        current_job: JobRequest | None = None,
        last_result: JobResult | None = None,
        force: bool = False,
    ) -> bool:
        """
        Publish one heartbeat.

        Returns True when a file was written. Returns False when a normal
        publication was skipped because interval_s has not elapsed.
        """
        now_monotonic = time.monotonic()

        if (
            not force
            and self._last_publish_monotonic is not None
            and (
                now_monotonic - self._last_publish_monotonic
                < self.interval_s
            )
        ):
            return False

        self._sequence += 1

        payload: dict[str, Any] = {
            "schema_version": 1,
            "application": self.application_name,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started_at_utc": self.started_at_utc,
            "heartbeat_at_utc": utc_now_text(),
            "heartbeat_sequence": self._sequence,
            "heartbeat_interval_s": self.interval_s,
            "loop_state": loop_state,
            "running": running,
            "paused": paused,
            "shutdown_requested": shutdown_requested,
            "current_job": self._job_payload(current_job),
            "last_job": self._result_payload(last_result),
        }

        self._write_atomically(payload)
        self._last_publish_monotonic = now_monotonic

        return True

    def _write_atomically(
        self,
        payload: dict[str, Any],
    ) -> None:
        self.status_dir.mkdir(parents=True, exist_ok=True)

        temporary_path = self.heartbeat_path.with_name(
            f".{self.heartbeat_filename}.{os.getpid()}.tmp"
        )

        temporary_path.write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        temporary_path.replace(self.heartbeat_path)

    @staticmethod
    def _job_payload(
        request: JobRequest | None,
    ) -> dict[str, Any] | None:
        if request is None:
            return None

        return {
            "kind": request.kind.value,
            "command_id": request.command_id,
            "origin": request.origin.value,
            "requested_at": request.requested_at.isoformat(),
        }

    @staticmethod
    def _result_payload(
        result: JobResult | None,
    ) -> dict[str, Any] | None:
        if result is None:
            return None

        return {
            "kind": result.request.kind.value,
            "command_id": result.request.command_id,
            "ok": result.ok,
            "message": result.message,
            "error": result.error,
        }
