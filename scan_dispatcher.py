# scan_dispatcher.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any

from export_gate import ExportGate
from scan_action_executor import (
    NoOpScanActionExecutor,
    ScanActionExecutor,
)
from scan_jobs import JobKind, JobRequest, JobResult


@dataclass
class ScanRuntimeFlags:
    """
    Minimal runtime state for v2 dispatcher testing.

    Export suspension is intentionally independent from the existing hard
    pause. The hard pause blocks all ToS work. Export suspension blocks only
    export jobs so Watchlist symbol updates remain available.
    """

    running: bool = False
    paused: bool = False
    shutdown_requested: bool = False
    exports_suspended: bool = False


class ScanDispatcher:
    """Execute JobRequest objects serially."""

    def __init__(
        self,
        *,
        flags: ScanRuntimeFlags | None = None,
        action_executor: ScanActionExecutor | None = None,
        logger: logging.Logger | None = None,
        export_gate: ExportGate | None = None,
    ) -> None:
        self.flags = (
            flags
            if flags is not None
            else ScanRuntimeFlags()
        )
        self.logger = logger
        self.action_executor = (
            action_executor
            if action_executor is not None
            else NoOpScanActionExecutor(
                logger=logger
            )
        )
        self.export_gate = export_gate
        self._action_lock = Lock()

    def execute(
        self,
        request: JobRequest,
    ) -> JobResult:
        """Execute one job under the dispatcher action lock."""

        with self._action_lock:
            return self._execute_locked(request)

    def _execute_locked(
        self,
        request: JobRequest,
    ) -> JobResult:
        self._log_info(
            "Dispatching job kind=%s origin=%s "
            "command_id=%s",
            request.kind.value,
            request.origin.value,
            request.command_id,
        )

        match request.kind:
            case JobKind.START:
                return self._handle_start(request)

            case JobKind.STOP:
                return self._handle_stop(request)

            case JobKind.PAUSE:
                return self._handle_pause(request)

            case JobKind.RESUME:
                return self._handle_resume(request)

            case JobKind.SUSPEND_EXPORTS:
                return self._handle_suspend_exports(
                    request
                )

            case JobKind.RESUME_EXPORTS:
                return self._handle_resume_exports(
                    request
                )

            case JobKind.REPLACE_WL_SYMBOLS:
                return (
                    self._handle_replace_wl_symbols(
                        request
                    )
                )

            case JobKind.ADD_WL_SYMBOLS:
                return self._handle_add_wl_symbols(
                    request
                )

            case JobKind.EXPORT_WL:
                return self._handle_export_wl(
                    request
                )

            case JobKind.EXPORT_TS:
                return self._handle_export_ts(
                    request
                )

            case JobKind.EXPORT_TM:
                return self._handle_export_tm(
                    request
                )

            case _:
                return self._fail(
                    request,
                    f"Unhandled job kind: "
                    f"{request.kind!r}",
                )

    def _handle_start(
        self,
        request: JobRequest,
    ) -> JobResult:
        self.flags.running = True
        self.flags.paused = False

        return self._ok(
            request,
            "Scanner marked running.",
        )

    def _handle_stop(
        self,
        request: JobRequest,
    ) -> JobResult:
        self.flags.running = False
        self.flags.paused = False
        self.flags.shutdown_requested = True

        return self._ok(
            request,
            "Scanner stop requested.",
        )

    def _handle_pause(
        self,
        request: JobRequest,
    ) -> JobResult:
        self.flags.paused = True

        return self._ok(
            request,
            "Scanner paused.",
        )

    def _handle_resume(
        self,
        request: JobRequest,
    ) -> JobResult:
        self.flags.running = True
        self.flags.paused = False

        return self._ok(
            request,
            "Scanner resumed.",
        )

    def _handle_suspend_exports(
        self,
        request: JobRequest,
    ) -> JobResult:
        try:
            if self.export_gate is not None:
                self.export_gate.suspend(
                    command_id=request.command_id,
                )

            self.flags.exports_suspended = True
        except Exception as exc:
            return self._fail(
                request,
                "Could not suspend scheduled "
                f"exports: {exc}",
                error=str(exc),
            )

        return self._ok(
            request,
            "Scheduled exports suspended.",
        )

    def _handle_resume_exports(
        self,
        request: JobRequest,
    ) -> JobResult:
        try:
            if self.export_gate is not None:
                self.export_gate.resume(
                    command_id=request.command_id,
                )

            self.flags.exports_suspended = False
        except Exception as exc:
            return self._fail(
                request,
                "Could not resume scheduled "
                f"exports: {exc}",
                error=str(exc),
            )

        return self._ok(
            request,
            "Scheduled exports resumed.",
        )

    def _handle_replace_wl_symbols(
        self,
        request: JobRequest,
    ) -> JobResult:
        if not self._can_run_work(request):
            return self._skipped_not_running(
                request
            )

        return (
            self.action_executor
            .replace_wl_symbols(request)
        )

    def _handle_add_wl_symbols(
        self,
        request: JobRequest,
    ) -> JobResult:
        if not self._can_run_work(request):
            return self._skipped_not_running(
                request
            )

        return (
            self.action_executor
            .add_wl_symbols(request)
        )

    def _handle_export_wl(
        self,
        request: JobRequest,
    ) -> JobResult:
        if self.flags.exports_suspended:
            return (
                self._skipped_exports_suspended(
                    request
                )
            )

        if not self._can_run_work(request):
            return self._skipped_not_running(
                request
            )

        return self.action_executor.export_wl(
            request
        )

    def _handle_export_ts(
        self,
        request: JobRequest,
    ) -> JobResult:
        if self.flags.exports_suspended:
            return (
                self._skipped_exports_suspended(
                    request
                )
            )

        if not self._can_run_work(request):
            return self._skipped_not_running(
                request
            )

        return self.action_executor.export_ts(
            request
        )

    def _handle_export_tm(
        self,
        request: JobRequest,
    ) -> JobResult:
        if self.flags.exports_suspended:
            return (
                self._skipped_exports_suspended(
                    request
                )
            )

        if not self._can_run_work(request):
            return self._skipped_not_running(
                request
            )

        return self.action_executor.export_tm(
            request
        )

    def _can_run_work(
        self,
        request: JobRequest,
    ) -> bool:
        del request

        return (
            self.flags.running
            and not self.flags.paused
        )

    def _skipped_not_running(
        self,
        request: JobRequest,
    ) -> JobResult:
        if self.flags.paused:
            return self._ok(
                request,
                "Skipped because scanner is paused.",
            )

        return self._ok(
            request,
            "Skipped because scanner is not "
            "running.",
        )

    def _skipped_exports_suspended(
        self,
        request: JobRequest,
    ) -> JobResult:
        return self._ok(
            request,
            "Skipped because scheduled exports "
            "are suspended.",
        )

    def _ok(
        self,
        request: JobRequest,
        message: str,
        **extra: Any,
    ) -> JobResult:
        self._log_info(
            "Job OK: %s",
            message,
        )

        return JobResult(
            request=request,
            ok=True,
            message=message,
            output_path=extra.get(
                "output_path"
            ),
        )

    def _fail(
        self,
        request: JobRequest,
        message: str,
        *,
        error: str | None = None,
    ) -> JobResult:
        self._log_error(
            "Job FAILED: %s",
            message,
        )

        return JobResult(
            request=request,
            ok=False,
            message=message,
            error=error or message,
        )

    def _log_info(
        self,
        msg: str,
        *args: Any,
    ) -> None:
        if self.logger is not None:
            self.logger.info(msg, *args)

    def _log_error(
        self,
        msg: str,
        *args: Any,
    ) -> None:
        if self.logger is not None:
            self.logger.error(msg, *args)
