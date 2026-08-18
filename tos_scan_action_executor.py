# tos_scan_action_executor.py

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Protocol
import time

from scan_action_executor import NoOpScanActionExecutor
from scan_jobs import JobRequest, JobResult, SourceKind
from scan_output import build_output_path


class ToSExportController(Protocol):
    def replace_watchlist_symbols(self, symbols: str) -> None:
        ...

    def add_watchlist_symbols(self, symbols: str) -> None:
        ...

    def open_watchlist_export(self) -> None:
        ...

    def normalize_watchlist_export_dialog(self) -> None:
        ...

    def enter_watchlist_filename_then_export_directory(
        self,
        filename: str,
        target_dir: str | Path,
    ) -> None:
        ...

    def confirm_watchlist_save(self) -> None:
        ...

    def export_csv_file(self) -> None:
        ...

    def normalize_scan_export_dialog(self) -> None:
        ...

    def enter_filename(self, filename: str, target_dir: str | Path) -> None:
        ...

    def enter_filename_then_export_directory(
        self,
        filename: str,
        target_dir: str | Path,
    ) -> None:
        ...

    def confirm_save(self) -> None:
        ...


class ToSScanActionExecutor(NoOpScanActionExecutor):
    """
    ToS-backed executor for v2 scan jobs.

    Currently wired:
        REPLACE_WL_SYMBOLS -> real ThinkOrSwim clipboard import
        ADD_WL_SYMBOLS -> real ThinkOrSwim clipboard import
        EXPORT_WL -> real ThinkOrSwim watchlist export
        EXPORT_TS -> real ThinkOrSwim scanner export
        EXPORT_TM -> real ThinkOrSwim scanner export, TM filename/source
    """

    def __init__(
        self,
        *,
        action_controller: ToSExportController | None,
        output_dir: Path,
        lan_scans_dir: Path | None = None,
        logger: logging.Logger | None = None,
        dry_run: bool = False,
    ) -> None:
        super().__init__(logger=logger)
        self.action_controller = action_controller
        self.output_dir = Path(output_dir)
        self.lan_scans_dir = (
            Path(lan_scans_dir)
            if lan_scans_dir is not None
            else None
        )
        self.dry_run = dry_run

    def _wait_for_output_file(self, output_path: Path, *, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s

        while time.monotonic() < deadline:
            if output_path.exists() and output_path.is_file():
                return True

            time.sleep(0.25)

        return False

    def replace_wl_symbols(self, request: JobRequest) -> JobResult:
        """
        Replace the Default ThinkOrSwim watchlist using clipboard import.
        """
        return self._apply_wl_symbol_change(request, mode="replace")

    def add_wl_symbols(self, request: JobRequest) -> JobResult:
        """
        Add symbols to the Default ThinkOrSwim watchlist using clipboard import.
        """
        return self._apply_wl_symbol_change(request, mode="add")

    def _apply_wl_symbol_change(
        self,
        request: JobRequest,
        *,
        mode: str,
    ) -> JobResult:
        if mode not in {"replace", "add"}:
            raise ValueError(f"Unsupported watchlist symbol mode: {mode!r}")

        try:
            symbols = self._symbols_from_request(request)
            symbol_text = "\n".join(symbols)
            symbol_count = len(symbols)
            symbol_word = "symbol" if symbol_count == 1 else "symbols"

            if self.dry_run:
                if mode == "replace":
                    message = (
                        f"DRY RUN: would replace Default WL with "
                        f"{symbol_count} {symbol_word}."
                    )
                else:
                    message = (
                        f"DRY RUN: would add {symbol_count} {symbol_word} "
                        f"to Default WL."
                    )

                self._log_info(message)

                return JobResult(
                    request=request,
                    ok=True,
                    message=message,
                )

            if self.action_controller is None:
                raise RuntimeError("No action_controller was provided.")

            if mode == "replace":
                self.action_controller.replace_watchlist_symbols(symbol_text)
            else:
                self.action_controller.add_watchlist_symbols(symbol_text)

        except Exception as exc:
            action_label = "Replace" if mode == "replace" else "Add"
            message = f"{action_label} WL symbols failed: {exc}"
            self._log_error(message)

            return JobResult(
                request=request,
                ok=False,
                message=message,
                error=str(exc),
            )

        if mode == "replace":
            message = (
                f"Replaced Default WL with "
                f"{symbol_count} {symbol_word}."
            )
        else:
            message = (
                f"Added {symbol_count} {symbol_word} "
                f"to Default WL."
            )

        self._log_info(message)

        return JobResult(
            request=request,
            ok=True,
            message=message,
        )

    def _symbols_from_request(self, request: JobRequest) -> list[str]:
        """
        Load, normalize, and de-duplicate symbols from a JobRequest.

        A symbol_file is read by Python and placed on the clipboard; it does
        not use ThinkOrSwim's file-selection dialog.
        """
        raw_values: list[str] = []

        if request.symbol_file is not None:
            symbol_file = Path(request.symbol_file)

            if not symbol_file.is_file():
                raise RuntimeError(
                    f"Watchlist symbol file does not exist: {symbol_file}"
                )

            raw_values.append(
                symbol_file.read_text(encoding="utf-8-sig")
            )
        else:
            raw_values.extend(request.symbols)

        symbols: list[str] = []
        seen: set[str] = set()

        for value in raw_values:
            for raw_symbol in value.replace(",", " ").split():
                symbol = raw_symbol.strip().upper()

                if symbol and symbol not in seen:
                    symbols.append(symbol)
                    seen.add(symbol)

        if not symbols:
            raise RuntimeError("No watchlist symbols were provided.")

        return symbols

    def export_wl(self, request: JobRequest) -> JobResult:
        """
        Export a WL CSV.

        This is intentionally thin. The real ToS details should remain in the
        existing pwidget/action-controller layer.
        """

        output_path = build_output_path(
            output_dir=self.output_dir,
            source=SourceKind.WL,
            when=request.requested_at,
            target_filename=request.target_filename,
        )
        
        if self.dry_run:
            output_path.parent.mkdir(parents=True, exist_ok=True)

            output_path.write_text(
                "Symbol,Source,Note\n"
                "DRYRUN,WL,Generated by ToSScanActionExecutor dry run\n",
                encoding="utf-8",
            )

            message = f"DRY RUN: wrote stub WL CSV to {output_path}"
            self._log_info(message)

            return JobResult(
                request=request,
                ok=True,
                message=message,
                output_path=output_path,
            )

        try:
            self._export_wl_with_existing_controller(output_path)

            if not self._wait_for_output_file(output_path, timeout_s=5.0):
                raise RuntimeError(
                    f"WL export file did not appear: {output_path}"
                )

            if (
                request.target_filename is not None
                and self.lan_scans_dir is not None
            ):
                verification_dir = self.lan_scans_dir / "watchlist_verify"
                verification_dir.mkdir(parents=True, exist_ok=True)

                verification_path = verification_dir / output_path.name
                shutil.copy2(output_path, verification_path)

                self._log_info(
                    f"Copied WL verification CSV to {verification_path}"
                )

        except Exception as exc:
            message = f"WL export failed: {exc}"
            self._log_error(message)

            return JobResult(
                request=request,
                ok=False,
                message=message,
                output_path=output_path,
                error=str(exc),
            )

        message = f"Exported WL CSV to {output_path}"
        self._log_info(message)

        return JobResult(
            request=request,
            ok=True,
            message=message,
            output_path=output_path,
        )

    def export_ts(self, request: JobRequest) -> JobResult:
        """
        Export a TS CSV through the existing scanner export dialog actions.
        """
        return self._export_scanner_source(request, SourceKind.TS)

    def export_tm(self, request: JobRequest) -> JobResult:
        """
        Export a TM CSV through the existing scanner export dialog actions.

        For now, TM uses the same ToS scanner export UI path as TS. The
        distinction is the output source/name.
        """
        return self._export_scanner_source(request, SourceKind.TM)

    def _export_scanner_source(
        self,
        request: JobRequest,
        source: SourceKind,
    ) -> JobResult:
        output_path = build_output_path(
            output_dir=self.output_dir,
            source=source,
            when=request.requested_at,
            target_filename=request.target_filename,
        )

        source_label = source.value

        if self.dry_run:
            output_path.parent.mkdir(parents=True, exist_ok=True)

            output_path.write_text(
                "Symbol,Source,Note\n"
                f"DRYRUN,{source_label},Generated by ToSScanActionExecutor dry run\n",
                encoding="utf-8",
            )

            message = f"DRY RUN: wrote stub {source_label} CSV to {output_path}"
            self._log_info(message)

            return JobResult(
                request=request,
                ok=True,
                message=message,
                output_path=output_path,
            )

        try:
            self._export_scanner_with_existing_controller(
                output_path,
                source=source,
            )
            if not self._wait_for_output_file(output_path, timeout_s=5.0):
                raise RuntimeError(
                    f"{source_label} export file did not appear: {output_path}"
                )

        except Exception as exc:
            message = f"{source_label} export failed: {exc}"
            self._log_error(message)

            return JobResult(
                request=request,
                ok=False,
                message=message,
                output_path=output_path,
                error=str(exc),
            )

        message = f"Exported {source_label} CSV to {output_path}"
        self._log_info(message)

        return JobResult(
            request=request,
            ok=True,
            message=message,
            output_path=output_path,
        )


    def _export_wl_with_existing_controller(self, output_path: Path) -> None:
        """
        Bridge v2 EXPORT_WL into the existing ToS watchlist export actions.

        Conservative sequence:
            open_watchlist_export()
            normalize_watchlist_export_dialog()
            enter_watchlist_filename_then_export_directory(filename, target_dir)
            confirm_watchlist_save()

        We set filename first, then directory, because changing the directory
        field can move/affect the filename field in the export dialog.
        """

        if self.action_controller is None:
            raise RuntimeError("No action_controller was provided.")

        controller = self.action_controller

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        filename = output_path.name
        target_dir = output_path.parent

        self._log_info(f"WL export target filename: {filename}")
        self._log_info(f"WL export target directory: {target_dir}")

        controller.open_watchlist_export()
        controller.normalize_watchlist_export_dialog()
        controller.enter_watchlist_filename_then_export_directory(
            filename,
            target_dir,
        )
        controller.confirm_watchlist_save()


    def _export_scanner_with_existing_controller(
        self,
        output_path: Path,
        *,
        source: SourceKind,
    ) -> None:
        """
        Bridge v2 scanner-source exports into the existing ToS scanner export actions.
        """

        if self.action_controller is None:
            raise RuntimeError("No action_controller was provided.")

        controller = self.action_controller

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        filename = output_path.name
        target_dir = output_path.parent
        source_label = source.value

        self._log_info(f"{source_label} export target filename: {filename}")
        self._log_info(f"{source_label} export target directory: {target_dir}")

        controller.export_csv_file()
        controller.normalize_scan_export_dialog()
        controller.enter_filename_then_export_directory(
            filename,
            target_dir,
        )
        controller.confirm_save()

    def _log_info(self, message: str) -> None:
        if self.logger is not None:
            self.logger.info(message)

    def _log_error(self, message: str) -> None:
        if self.logger is not None:
            self.logger.error(message)

