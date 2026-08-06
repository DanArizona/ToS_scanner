# scanner_heartbeat.py

from __future__ import annotations

import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scan_jobs import JobRequest, JobResult


LOGGER_NAME = "scan_command_loop"


def utc_now_text() -> str:
    """Return the current UTC time in compact ISO-8601 form."""

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

    Windows and SMB readers can briefly prevent replacement of the existing
    heartbeat file. Such PermissionError failures are retried with bounded
    exponential backoff. If the file remains locked, publication is logged
    and skipped so the scanner command loop can continue running.
    """

    command_root: Path
    interval_s: float = 5.0
    application_name: str = "ToS_scanner"
    status_directory_name: str = "status"
    heartbeat_filename: str = (
        "scanner_heartbeat.json"
    )
    replace_retry_attempts: int = 6
    replace_retry_initial_delay_s: float = (
        0.05
    )
    replace_retry_max_delay_s: float = 0.5

    started_at_utc: str = field(
        default_factory=utc_now_text
    )
    _sequence: int = field(
        default=0,
        init=False,
    )
    _last_publish_monotonic: (
        float | None
    ) = field(
        default=None,
        init=False,
    )

    def __post_init__(self) -> None:
        self.command_root = Path(
            self.command_root
        )

        if self.interval_s <= 0:
            raise ValueError(
                "interval_s must be greater than "
                "zero."
            )

        if self.replace_retry_attempts < 1:
            raise ValueError(
                "replace_retry_attempts must be at "
                "least one."
            )

        if (
            self.replace_retry_initial_delay_s
            < 0
        ):
            raise ValueError(
                "replace_retry_initial_delay_s "
                "cannot be negative."
            )

        if self.replace_retry_max_delay_s < 0:
            raise ValueError(
                "replace_retry_max_delay_s cannot "
                "be negative."
            )

        if (
            self.replace_retry_max_delay_s
            < self.replace_retry_initial_delay_s
        ):
            raise ValueError(
                "replace_retry_max_delay_s cannot "
                "be less than "
                "replace_retry_initial_delay_s."
            )

        self.status_dir = (
            self.command_root
            / self.status_directory_name
        )
        self.heartbeat_path = (
            self.status_dir
            / self.heartbeat_filename
        )

    def publish(
        self,
        *,
        running: bool,
        paused: bool,
        shutdown_requested: bool,
        loop_state: str,
        exports_suspended: bool = False,
        current_job: JobRequest | None = None,
        last_result: JobResult | None = None,
        force: bool = False,
    ) -> bool:
        """
        Publish one heartbeat.

        Returns True when a file was written.

        Returns False when a normal publication was rate-limited or when
        transient Windows/SMB PermissionError failures exhausted the bounded
        retry policy. A skipped heartbeat never terminates the command loop.
        """

        now_monotonic = time.monotonic()

        if (
            not force
            and self._last_publish_monotonic
            is not None
            and (
                now_monotonic
                - self._last_publish_monotonic
                < self.interval_s
            )
        ):
            return False

        self._sequence += 1

        payload: dict[str, Any] = {
            "schema_version": 1,
            "application": (
                self.application_name
            ),
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started_at_utc": (
                self.started_at_utc
            ),
            "heartbeat_at_utc": (
                utc_now_text()
            ),
            "heartbeat_sequence": (
                self._sequence
            ),
            "heartbeat_interval_s": (
                self.interval_s
            ),
            "loop_state": loop_state,
            "running": running,
            "paused": paused,
            "exports_suspended": (
                exports_suspended
            ),
            "shutdown_requested": (
                shutdown_requested
            ),
            "current_job": self._job_payload(
                current_job
            ),
            "last_job": self._result_payload(
                last_result
            ),
        }

        written = self._write_atomically(
            payload
        )

        # Throttle the next normal attempt even if this attempt was skipped
        # after exhausting transient-lock retries. Forced state transitions
        # can still try immediately.
        self._last_publish_monotonic = (
            now_monotonic
        )

        return written

    def _write_atomically(
        self,
        payload: dict[str, Any],
    ) -> bool:
        self.status_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        serialized = (
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

        delay_s = (
            self.replace_retry_initial_delay_s
        )
        last_error: (
            PermissionError | None
        ) = None

        for attempt in range(
            1,
            self.replace_retry_attempts + 1,
        ):
            temporary_path = (
                self._temporary_path(attempt)
            )

            try:
                temporary_path.write_text(
                    serialized,
                    encoding="utf-8",
                )
                temporary_path.replace(
                    self.heartbeat_path
                )
                return True
            except PermissionError as exc:
                last_error = exc
                self._remove_temporary_file(
                    temporary_path
                )

                if (
                    attempt
                    >= self.replace_retry_attempts
                ):
                    break

                if delay_s > 0:
                    time.sleep(delay_s)
                    delay_s = min(
                        delay_s * 2,
                        self.replace_retry_max_delay_s,
                    )
            except Exception:
                self._remove_temporary_file(
                    temporary_path
                )
                raise

        assert last_error is not None

        logging.getLogger(
            LOGGER_NAME
        ).error(
            "Heartbeat publication failed "
            "after %d attempt(s); scanner loop "
            "will continue and retry on a "
            "later publication: %s",
            self.replace_retry_attempts,
            last_error,
        )

        return False

    def _temporary_path(
        self,
        attempt: int,
    ) -> Path:
        return self.heartbeat_path.with_name(
            f".{self.heartbeat_filename}."
            f"{os.getpid()}."
            f"{self._sequence}."
            f"{attempt}.tmp"
        )

    @staticmethod
    def _remove_temporary_file(
        temporary_path: Path,
    ) -> None:
        try:
            temporary_path.unlink(
                missing_ok=True
            )
        except OSError:
            # Cleanup is best-effort. Never
            # replace the useful original
            # exception with a temporary-file
            # cleanup failure.
            pass

    @staticmethod
    def _job_payload(
        request: JobRequest | None,
    ) -> dict[str, Any] | None:
        if request is None:
            return None

        return {
            "kind": request.kind.value,
            "command_id": (
                request.command_id
            ),
            "origin": (
                request.origin.value
            ),
            "requested_at": (
                request.requested_at
                .isoformat()
            ),
        }

    @staticmethod
    def _result_payload(
        result: JobResult | None,
    ) -> dict[str, Any] | None:
        if result is None:
            return None

        return {
            "kind": (
                result.request.kind.value
            ),
            "command_id": (
                result.request.command_id
            ),
            "ok": result.ok,
            "message": result.message,
            "error": result.error,
        }
