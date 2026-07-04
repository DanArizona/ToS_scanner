# scan_action_executor.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from scan_jobs import JobRequest, JobResult


class ScanActionExecutor(Protocol):
    """
    Interface for scan job execution.

    The dispatcher controls job ordering and runtime state.
    The executor performs actual work.

    First implementation is no-op. Later, a ToS executor can implement this
    same interface using pwidget actions.
    """

    def replace_wl_symbols(self, request: JobRequest) -> JobResult:
        ...

    def add_wl_symbols(self, request: JobRequest) -> JobResult:
        ...

    def export_wl(self, request: JobRequest) -> JobResult:
        ...

    def export_ts(self, request: JobRequest) -> JobResult:
        ...

    def export_tm(self, request: JobRequest) -> JobResult:
        ...


@dataclass
class NoOpScanActionExecutor:
    """
    Safe executor used during v2 control-plane testing.

    This does not touch ThinkOrSwim.
    """

    logger: logging.Logger | None = None

    def replace_wl_symbols(self, request: JobRequest) -> JobResult:
        if request.symbol_file is not None:
            message = f"Would replace WL symbols from file: {request.symbol_file}"
        else:
            message = (
                f"Would replace WL symbols from explicit list: "
                f"{len(request.symbols)} symbols"
            )

        return self._ok(request, message)

    def add_wl_symbols(self, request: JobRequest) -> JobResult:
        if request.symbol_file is not None:
            message = f"Would add WL symbols from file: {request.symbol_file}"
        else:
            message = (
                f"Would add WL symbols from explicit list: "
                f"{len(request.symbols)} symbols"
            )

        return self._ok(request, message)

    def export_wl(self, request: JobRequest) -> JobResult:
        return self._ok(request, "Would export WL CSV.")

    def export_ts(self, request: JobRequest) -> JobResult:
        return self._ok(request, "Would export TS CSV.")

    def export_tm(self, request: JobRequest) -> JobResult:
        return self._ok(request, "Would export TM CSV.")

    def _ok(self, request: JobRequest, message: str) -> JobResult:
        if self.logger is not None:
            self.logger.info("Job OK: %s", message)

        return JobResult(
            request=request,
            ok=True,
            message=message,
        )
    
