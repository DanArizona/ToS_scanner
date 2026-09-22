from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from scan_command_loop import (
    DEFAULT_COMMAND_ROOT,
    DISPLAY_ONLY_ALLOWED_JOBS,
    ENV_SCAN_CONTROL,
    _display_only_rejection,
    parse_args,
    resolve_command_root,
)
from scan_jobs import JobKind, JobOrigin, JobRequest


def test_parse_args_accepts_explicit_root() -> None:
    args = parse_args(
        [
            "--root",
            r"C:\ScannerControl",
        ]
    )

    assert args.root == Path(r"C:\ScannerControl")


def test_parse_args_accepts_display_only() -> None:
    args = parse_args(["--display-only"])

    assert args.display_only is True


def test_display_only_allows_full_snapshot_replace() -> None:
    assert JobKind.REPLACE_WL_SYMBOLS in DISPLAY_ONLY_ALLOWED_JOBS
    assert JobKind.ADD_WL_SYMBOLS not in DISPLAY_ONLY_ALLOWED_JOBS
    assert JobKind.EXPORT_WL not in DISPLAY_ONLY_ALLOWED_JOBS
    assert JobKind.RESUME_EXPORTS not in DISPLAY_ONLY_ALLOWED_JOBS


def test_display_only_rejection_is_explicit() -> None:
    request = JobRequest(
        kind=JobKind.EXPORT_WL,
        origin=JobOrigin.FILE_COMMAND,
        requested_at=datetime(
            2026,
            9,
            22,
            tzinfo=timezone.utc,
        ),
        command_id="test-display-only-rejection",
    )

    result = _display_only_rejection(request)

    assert result.ok is False
    assert "Display-only mode rejected export_wl" in result.message
    assert "disabled" in (result.error or "")


def test_resolve_command_root_prefers_explicit_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        ENV_SCAN_CONTROL,
        r"C:\EnvironmentControl",
    )

    root = resolve_command_root(
        Path(r"C:\ExplicitControl")
    )

    assert root == Path(r"C:\ExplicitControl")


def test_resolve_command_root_uses_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        ENV_SCAN_CONTROL,
        r"C:\EnvironmentControl",
    )

    root = resolve_command_root(None)

    assert root == Path(r"C:\EnvironmentControl")


def test_resolve_command_root_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(
        ENV_SCAN_CONTROL,
        raising=False,
    )

    root = resolve_command_root(None)

    assert root == DEFAULT_COMMAND_ROOT


def test_resolve_command_root_ignores_blank_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        ENV_SCAN_CONTROL,
        "   ",
    )

    root = resolve_command_root(None)

    assert root == DEFAULT_COMMAND_ROOT
