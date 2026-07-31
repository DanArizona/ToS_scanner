from __future__ import annotations

from pathlib import Path

import pytest

from scan_command_loop import (
    DEFAULT_COMMAND_ROOT,
    ENV_SCAN_CONTROL,
    parse_args,
    resolve_command_root,
)


def test_parse_args_accepts_explicit_root() -> None:
    args = parse_args(
        [
            "--root",
            r"C:\ScannerControl",
        ]
    )

    assert args.root == Path(r"C:\ScannerControl")


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
