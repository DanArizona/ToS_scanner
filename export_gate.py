"""Shared export-suspension state and interprocess export locking."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from types import TracebackType
from typing import Any, BinaryIO


STATUS_DIRECTORY_NAME = "status"
EXPORT_GATE_FILENAME = "export_gate.json"
EXPORT_ACTION_LOCK_FILENAME = "export_action.lock"


def utc_now_text() -> str:
    """Return the current UTC time in compact ISO-8601 form."""

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


@dataclass(frozen=True)
class ExportGateSnapshot:
    """Persisted state controlling scheduled exports."""

    suspended: bool = False
    generation: int = 0
    updated_at_utc: str | None = None
    command_id: str | None = None
    error: str | None = None


class ExportActionLock:
    """One-byte, non-reentrant, cross-process exclusive file lock."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: BinaryIO | None = None

    @property
    def acquired(self) -> bool:
        """Return True when this object currently owns the lock."""

        return self._handle is not None

    def try_acquire(self) -> bool:
        """Attempt to acquire the lock once without waiting."""

        if self.acquired:
            raise RuntimeError(
                "This ExportActionLock already owns the lock."
            )

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)

        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()

        handle.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(
                    handle.fileno(),
                    msvcrt.LK_NBLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        except OSError:
            handle.close()
            return False

        self._handle = handle
        return True

    def acquire(
        self,
        *,
        timeout_s: float | None = None,
        poll_s: float = 0.05,
        stop_event: Event | None = None,
    ) -> bool:
        """
        Wait until the lock is acquired, timeout expires, or stop is set.

        A timeout of None waits indefinitely.
        """

        if timeout_s is not None and timeout_s < 0:
            raise ValueError(
                "timeout_s cannot be negative."
            )

        if poll_s <= 0:
            raise ValueError(
                "poll_s must be positive."
            )

        deadline = (
            None
            if timeout_s is None
            else time.monotonic() + timeout_s
        )

        while True:
            if self.try_acquire():
                return True

            if (
                stop_event is not None
                and stop_event.is_set()
            ):
                return False

            if (
                deadline is not None
                and time.monotonic() >= deadline
            ):
                return False

            time.sleep(poll_s)

    def release(self) -> None:
        """Release the lock when owned."""

        handle = self._handle

        if handle is None:
            return

        handle.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(
                    handle.fileno(),
                    msvcrt.LK_UNLCK,
                    1,
                )
            else:
                import fcntl

                fcntl.flock(
                    handle.fileno(),
                    fcntl.LOCK_UN,
                )
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> ExportActionLock:
        if not self.acquire():
            raise RuntimeError(
                f"Could not acquire export lock: {self.path}"
            )

        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.release()
        return False


class ExportGate:
    """
    Coordinate scheduled exports across independent scanner processes.

    The JSON state blocks new scheduled exports. The action lock serializes
    suspension against an export that may already be running.
    """

    def __init__(
        self,
        command_root: Path,
        *,
        replace_retry_attempts: int = 6,
        replace_retry_initial_delay_s: float = 0.05,
        replace_retry_max_delay_s: float = 0.5,
    ) -> None:
        self.command_root = Path(command_root)
        self.status_dir = (
            self.command_root / STATUS_DIRECTORY_NAME
        )
        self.state_path = (
            self.status_dir / EXPORT_GATE_FILENAME
        )
        self.action_lock_path = (
            self.status_dir
            / EXPORT_ACTION_LOCK_FILENAME
        )

        self.replace_retry_attempts = (
            replace_retry_attempts
        )
        self.replace_retry_initial_delay_s = (
            replace_retry_initial_delay_s
        )
        self.replace_retry_max_delay_s = (
            replace_retry_max_delay_s
        )

        self._suspension_lock: (
            ExportActionLock | None
        ) = None

        if self.replace_retry_attempts < 1:
            raise ValueError(
                "replace_retry_attempts must be at least one."
            )

        if self.replace_retry_initial_delay_s < 0:
            raise ValueError(
                "replace_retry_initial_delay_s cannot be negative."
            )

        if self.replace_retry_max_delay_s < 0:
            raise ValueError(
                "replace_retry_max_delay_s cannot be negative."
            )

        if (
            self.replace_retry_max_delay_s
            < self.replace_retry_initial_delay_s
        ):
            raise ValueError(
                "replace_retry_max_delay_s cannot be less than "
                "replace_retry_initial_delay_s."
            )

    def snapshot(self) -> ExportGateSnapshot:
        """
        Read the current state.

        Missing state means exports are active. Unreadable or invalid state
        fails closed by reporting suspension.
        """

        try:
            raw_text = self.state_path.read_text(
                encoding="utf-8"
            )
            payload = json.loads(raw_text)
        except FileNotFoundError:
            return ExportGateSnapshot()
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            return ExportGateSnapshot(
                suspended=True,
                error=(
                    "Export gate could not be read: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        if not isinstance(payload, dict):
            return ExportGateSnapshot(
                suspended=True,
                error=(
                    "Export gate payload is not a JSON object."
                ),
            )

        try:
            suspended = payload["suspended"]
            generation = payload["generation"]

            if not isinstance(suspended, bool):
                raise TypeError(
                    "'suspended' must be a boolean."
                )

            if (
                not isinstance(generation, int)
                or isinstance(generation, bool)
                or generation < 0
            ):
                raise TypeError(
                    "'generation' must be a nonnegative integer."
                )
        except (KeyError, TypeError) as exc:
            return ExportGateSnapshot(
                suspended=True,
                error=f"Invalid export gate payload: {exc}",
            )

        updated_at_utc = payload.get(
            "updated_at_utc"
        )
        command_id = payload.get("command_id")

        if (
            updated_at_utc is not None
            and not isinstance(updated_at_utc, str)
        ):
            return ExportGateSnapshot(
                suspended=True,
                error=(
                    "Invalid export gate payload: "
                    "'updated_at_utc' must be text or null."
                ),
            )

        if (
            command_id is not None
            and not isinstance(command_id, str)
        ):
            return ExportGateSnapshot(
                suspended=True,
                error=(
                    "Invalid export gate payload: "
                    "'command_id' must be text or null."
                ),
            )

        return ExportGateSnapshot(
            suspended=suspended,
            generation=generation,
            updated_at_utc=updated_at_utc,
            command_id=command_id,
        )

    def is_suspended(self) -> bool:
        """Return whether scheduled exports are currently blocked."""

        return self.snapshot().suspended

    def suspend(
        self,
        *,
        command_id: str | None,
        timeout_s: float = 30.0,
        poll_s: float = 0.05,
    ) -> ExportGateSnapshot:
        """
        Wait for any active export and establish persistent suspension.

        The action lock remains held until resume() or close().
        """

        if self._suspension_lock is None:
            lock = ExportActionLock(
                self.action_lock_path
            )

            if not lock.acquire(
                timeout_s=timeout_s,
                poll_s=poll_s,
            ):
                raise TimeoutError(
                    "Timed out waiting for the active "
                    "scheduled export to finish."
                )

            self._suspension_lock = lock

        current = self.snapshot()

        if current.suspended and current.error is None:
            return current

        updated = ExportGateSnapshot(
            suspended=True,
            generation=current.generation + 1,
            updated_at_utc=utc_now_text(),
            command_id=command_id,
        )

        try:
            self._write_snapshot(updated)
        except Exception:
            self._release_suspension_lock()
            raise

        return updated

    def resume(
        self,
        *,
        command_id: str | None,
        timeout_s: float = 30.0,
        poll_s: float = 0.05,
    ) -> ExportGateSnapshot:
        """
        Clear suspension and release the action lock.

        This also supports recovery after a command-loop restart, when the
        state is suspended but this object does not own the old process lock.
        """

        if self._suspension_lock is None:
            lock = ExportActionLock(
                self.action_lock_path
            )

            if not lock.acquire(
                timeout_s=timeout_s,
                poll_s=poll_s,
            ):
                raise TimeoutError(
                    "Timed out waiting for the export "
                    "action lock before resume."
                )

            self._suspension_lock = lock

        current = self.snapshot()

        if not current.suspended and current.error is None:
            self._release_suspension_lock()
            return current

        updated = ExportGateSnapshot(
            suspended=False,
            generation=current.generation + 1,
            updated_at_utc=utc_now_text(),
            command_id=command_id,
        )

        try:
            self._write_snapshot(updated)
        except Exception:
            # Keep the lock held. The failed resume remains fail-safe.
            raise

        self._release_suspension_lock()
        return updated

    def try_begin_export(self) -> ExportActionLock | None:
        """
        Try to begin one export without waiting.

        The state is checked before and after lock acquisition to close the
        race between a scheduler reaching a slot and a suspension request.
        """

        if self.is_suspended():
            return None

        lock = ExportActionLock(
            self.action_lock_path
        )

        if not lock.try_acquire():
            return None

        if self.is_suspended():
            lock.release()
            return None

        return lock

    def close(self) -> None:
        """
        Release a process-held lock without altering persistent state.

        A suspended state intentionally survives command-loop termination.
        """

        self._release_suspension_lock()

    def _release_suspension_lock(self) -> None:
        lock = self._suspension_lock

        if lock is not None:
            lock.release()
            self._suspension_lock = None

    def _write_snapshot(
        self,
        snapshot: ExportGateSnapshot,
    ) -> None:
        self.status_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload: dict[str, Any] = {
            "schema_version": 1,
            "suspended": snapshot.suspended,
            "generation": snapshot.generation,
            "updated_at_utc": (
                snapshot.updated_at_utc
            ),
            "command_id": snapshot.command_id,
        }

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
        last_error: PermissionError | None = None

        for attempt in range(
            1,
            self.replace_retry_attempts + 1,
        ):
            temporary_path = (
                self.state_path.with_name(
                    f".{self.state_path.name}."
                    f"{os.getpid()}."
                    f"{threading.get_ident()}."
                    f"{uuid.uuid4().hex}.tmp"
                )
            )

            try:
                temporary_path.write_text(
                    serialized,
                    encoding="utf-8",
                )
                temporary_path.replace(
                    self.state_path
                )
                return
            except PermissionError as exc:
                last_error = exc
                self._remove_temporary_file(
                    temporary_path
                )

                if attempt >= self.replace_retry_attempts:
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
        raise last_error

    @staticmethod
    def _remove_temporary_file(
        temporary_path: Path,
    ) -> None:
        try:
            temporary_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass
