from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from scan_jobs import JobKind, JobOrigin, JobRequest
from tos_scan_action_executor import ToSScanActionExecutor


class RecordingController:
    def __init__(self) -> None:
        self.replace_calls: list[str] = []
        self.add_calls: list[str] = []

    def replace_watchlist_symbols(self, symbols: str) -> None:
        self.replace_calls.append(symbols)

    def add_watchlist_symbols(self, symbols: str) -> None:
        self.add_calls.append(symbols)

    # The remaining methods satisfy the controller protocol. These tests
    # should never call any export-related method.
    def open_watchlist_export(self) -> None:
        raise AssertionError("Unexpected open_watchlist_export call")

    def normalize_watchlist_export_dialog(self) -> None:
        raise AssertionError(
            "Unexpected normalize_watchlist_export_dialog call"
        )

    def enter_watchlist_filename_then_export_directory(
        self,
        filename: str,
        target_dir: str | Path,
    ) -> None:
        raise AssertionError(
            "Unexpected enter_watchlist_filename_then_export_directory call"
        )

    def confirm_watchlist_save(self) -> None:
        raise AssertionError("Unexpected confirm_watchlist_save call")

    def export_csv_file(self) -> None:
        raise AssertionError("Unexpected export_csv_file call")

    def normalize_scan_export_dialog(self) -> None:
        raise AssertionError(
            "Unexpected normalize_scan_export_dialog call"
        )

    def enter_filename(
        self,
        filename: str,
        target_dir: str | Path,
    ) -> None:
        raise AssertionError("Unexpected enter_filename call")

    def enter_filename_then_export_directory(
        self,
        filename: str,
        target_dir: str | Path,
    ) -> None:
        raise AssertionError(
            "Unexpected enter_filename_then_export_directory call"
        )

    def confirm_save(self) -> None:
        raise AssertionError("Unexpected confirm_save call")


def make_request(
    *,
    kind: JobKind,
    symbols: list[str] | None = None,
    symbol_file: Path | None = None,
    target_filename: str | None = None,
) -> JobRequest:
    return JobRequest(
        kind=kind,
        origin=JobOrigin.FILE_COMMAND,
        requested_at=datetime(2026, 7, 20, 5, 0, 0),
        symbols=list(symbols or []),
        symbol_file=symbol_file,
        target_filename=target_filename,
        command_id="test-command",
    )


def test_replace_wl_symbols_normalizes_and_calls_controller(
    tmp_path: Path,
) -> None:
    controller = RecordingController()
    executor = ToSScanActionExecutor(
        action_controller=controller,
        output_dir=tmp_path,
    )
    request = make_request(
        kind=JobKind.REPLACE_WL_SYMBOLS,
        symbols=["aapl,msft", "NVDA", "aapl"],
    )

    result = executor.replace_wl_symbols(request)

    assert result.ok is True
    assert result.error is None
    assert result.message == "Replaced Default WL with 3 symbols."
    assert controller.replace_calls == ["AAPL\nMSFT\nNVDA"]
    assert controller.add_calls == []


def test_add_wl_symbols_normalizes_and_calls_controller(
    tmp_path: Path,
) -> None:
    controller = RecordingController()
    executor = ToSScanActionExecutor(
        action_controller=controller,
        output_dir=tmp_path,
    )
    request = make_request(
        kind=JobKind.ADD_WL_SYMBOLS,
        symbols=["tsla", "AMD,NVDA", "TSLA"],
    )

    result = executor.add_wl_symbols(request)

    assert result.ok is True
    assert result.error is None
    assert result.message == "Added 3 symbols to Default WL."
    assert controller.add_calls == ["TSLA\nAMD\nNVDA"]
    assert controller.replace_calls == []


def test_replace_wl_symbols_reads_symbol_file(
    tmp_path: Path,
) -> None:
    symbol_file = tmp_path / "symbols.txt"
    symbol_file.write_text(
        "\ufeffaapl, msft\nNVDA\nAAPL\n",
        encoding="utf-8",
    )

    controller = RecordingController()
    executor = ToSScanActionExecutor(
        action_controller=controller,
        output_dir=tmp_path,
    )
    request = make_request(
        kind=JobKind.REPLACE_WL_SYMBOLS,
        symbol_file=symbol_file,
    )

    result = executor.replace_wl_symbols(request)

    assert result.ok is True
    assert controller.replace_calls == ["AAPL\nMSFT\nNVDA"]


def test_symbol_change_dry_run_does_not_require_controller(
    tmp_path: Path,
) -> None:
    executor = ToSScanActionExecutor(
        action_controller=None,
        output_dir=tmp_path,
        dry_run=True,
    )
    request = make_request(
        kind=JobKind.ADD_WL_SYMBOLS,
        symbols=["AAPL", "MSFT"],
    )

    result = executor.add_wl_symbols(request)

    assert result.ok is True
    assert result.error is None
    assert result.message == (
        "DRY RUN: would add 2 symbols to Default WL."
    )


def test_symbol_change_without_controller_returns_failure(
    tmp_path: Path,
) -> None:
    executor = ToSScanActionExecutor(
        action_controller=None,
        output_dir=tmp_path,
    )
    request = make_request(
        kind=JobKind.REPLACE_WL_SYMBOLS,
        symbols=["AAPL"],
    )

    result = executor.replace_wl_symbols(request)

    assert result.ok is False
    assert result.error == "No action_controller was provided."
    assert result.message == (
        "Replace WL symbols failed: "
        "No action_controller was provided."
    )


def test_symbol_change_rejects_empty_symbol_list(
    tmp_path: Path,
) -> None:
    controller = RecordingController()
    executor = ToSScanActionExecutor(
        action_controller=controller,
        output_dir=tmp_path,
    )
    request = make_request(
        kind=JobKind.ADD_WL_SYMBOLS,
        symbols=[],
    )

    result = executor.add_wl_symbols(request)

    assert result.ok is False
    assert result.error == "No watchlist symbols were provided."
    assert controller.replace_calls == []
    assert controller.add_calls == []


def test_add_wl_symbols_uses_singular_word_for_one_symbol(
    tmp_path: Path,
) -> None:
    controller = RecordingController()
    executor = ToSScanActionExecutor(
        action_controller=controller,
        output_dir=tmp_path,
    )
    request = make_request(
        kind=JobKind.ADD_WL_SYMBOLS,
        symbols=["AMD"],
    )

    result = executor.add_wl_symbols(request)

    assert result.ok is True
    assert result.error is None
    assert result.message == "Added 1 symbol to Default WL."
    assert controller.add_calls == ["AMD"]
    assert controller.replace_calls == []


def test_export_wl_with_target_filename_copies_verification_csv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "local_scans"
    lan_scans_dir = tmp_path / "lan_scans"

    executor = ToSScanActionExecutor(
        action_controller=None,
        output_dir=output_dir,
        lan_scans_dir=lan_scans_dir,
    )

    request = make_request(
        kind=JobKind.EXPORT_WL,
        target_filename="objective2-test-WL.csv",
    )

    def fake_export(output_path: Path) -> None:
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        output_path.write_text(
            "Symbol,Value\n"
            "NVDA,1\n"
            "TSLA,2\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        executor,
        "_export_wl_with_existing_controller",
        fake_export,
    )

    result = executor.export_wl(request)

    local_path = (
        output_dir
        / "objective2-test-WL.csv"
    )
    verification_path = (
        lan_scans_dir
        / "watchlist_verify"
        / "objective2-test-WL.csv"
    )

    assert result.ok is True
    assert result.error is None

    assert local_path.exists()
    assert verification_path.exists()

    assert (
        verification_path.read_text(
            encoding="utf-8"
        )
        == local_path.read_text(
            encoding="utf-8"
        )
    )