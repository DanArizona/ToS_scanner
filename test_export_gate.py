from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import export_gate
from export_gate import (
    ExportActionLock,
    ExportGate,
)


def test_missing_gate_defaults_to_exports_active(
    tmp_path: Path,
) -> None:
    gate = ExportGate(tmp_path)

    snapshot = gate.snapshot()

    assert snapshot.suspended is False
    assert snapshot.generation == 0
    assert snapshot.error is None


def test_invalid_gate_fails_closed(
    tmp_path: Path,
) -> None:
    gate = ExportGate(tmp_path)
    gate.status_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    gate.state_path.write_text(
        "{not valid JSON",
        encoding="utf-8",
    )

    snapshot = gate.snapshot()

    assert snapshot.suspended is True
    assert snapshot.error is not None


def test_suspend_and_resume_persist_state(
    tmp_path: Path,
) -> None:
    controller = ExportGate(tmp_path)
    observer = ExportGate(tmp_path)

    suspended = controller.suspend(
        command_id="suspend-1",
    )

    assert suspended.suspended is True
    assert suspended.generation == 1
    assert observer.is_suspended() is True
    assert observer.try_begin_export() is None

    resumed = controller.resume(
        command_id="resume-1",
    )

    assert resumed.suspended is False
    assert resumed.generation == 2
    assert observer.is_suspended() is False

    lease = observer.try_begin_export()

    assert lease is not None
    lease.release()


def test_suspended_state_survives_controller_close(
    tmp_path: Path,
) -> None:
    first = ExportGate(tmp_path)
    first.suspend(
        command_id="suspend-2",
    )
    first.close()

    second = ExportGate(tmp_path)

    assert second.is_suspended() is True

    second.resume(
        command_id="resume-2",
    )

    assert second.is_suspended() is False


def test_action_lock_is_cross_process(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "export.lock"
    parent_lock = ExportActionLock(lock_path)

    assert parent_lock.try_acquire() is True

    child_code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from export_gate import ExportActionLock\n"
        "lock = ExportActionLock(Path(sys.argv[1]))\n"
        "ok = lock.try_acquire()\n"
        "print('acquired' if ok else 'blocked')\n"
        "lock.release()\n"
    )

    blocked = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            str(lock_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert blocked.stdout.strip() == "blocked"

    parent_lock.release()

    acquired = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            str(lock_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert acquired.stdout.strip() == "acquired"


def test_atomic_write_retries_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = ExportGate(
        tmp_path,
        replace_retry_attempts=4,
        replace_retry_initial_delay_s=0.01,
        replace_retry_max_delay_s=0.10,
    )
    original_replace = Path.replace
    attempts = 0
    delays: list[float] = []

    def flaky_replace(
        source: Path,
        target: Path,
    ) -> Path:
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            raise PermissionError(
                "simulated transient lock"
            )

        return original_replace(
            source,
            target,
        )

    monkeypatch.setattr(
        Path,
        "replace",
        flaky_replace,
    )
    monkeypatch.setattr(
        export_gate.time,
        "sleep",
        delays.append,
    )

    snapshot = gate.suspend(
        command_id="suspend-3",
    )

    assert snapshot.suspended is True
    assert attempts == 3
    assert delays == [0.01, 0.02]
    assert list(
        gate.status_dir.glob(
            ".export_gate.json.*.tmp"
        )
    ) == []

    gate.resume(
        command_id="resume-3",
    )


def test_exhausted_permission_error_leaves_no_temp_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = ExportGate(
        tmp_path,
        replace_retry_attempts=2,
        replace_retry_initial_delay_s=0,
        replace_retry_max_delay_s=0,
    )

    def locked_replace(
        source: Path,
        target: Path,
    ) -> Path:
        raise PermissionError(
            "simulated persistent lock"
        )

    monkeypatch.setattr(
        Path,
        "replace",
        locked_replace,
    )

    with pytest.raises(
        PermissionError,
        match="persistent lock",
    ):
        gate.suspend(
            command_id="suspend-4",
        )

    assert list(
        gate.status_dir.glob(
            ".export_gate.json.*.tmp"
        )
    ) == []

    # Failed suspension released the action lock.
    independent = ExportActionLock(
        gate.action_lock_path
    )
    assert independent.try_acquire() is True
    independent.release()


def test_gate_file_has_expected_schema(
    tmp_path: Path,
) -> None:
    gate = ExportGate(tmp_path)
    gate.suspend(
        command_id="suspend-schema",
    )

    payload = json.loads(
        gate.state_path.read_text(
            encoding="utf-8"
        )
    )

    assert payload["schema_version"] == 1
    assert payload["suspended"] is True
    assert payload["generation"] == 1
    assert payload["command_id"] == (
        "suspend-schema"
    )
    assert payload["updated_at_utc"].endswith(
        "Z"
    )

    gate.resume(
        command_id="resume-schema",
    )
