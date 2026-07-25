# file_command_ingress.py

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from scan_job_queue import ScanJobQueue
from scan_jobs import JobKind, JobOrigin, JobRequest, SourceKind


class CommandParseError(ValueError):
    """Raised when a command file cannot be converted into a JobRequest."""


_JOB_KIND_BY_TEXT: dict[str, JobKind] = {
    kind.value.lower(): kind for kind in JobKind
} | {
    kind.name.lower(): kind for kind in JobKind
}


_SOURCE_KIND_BY_TEXT: dict[str, SourceKind] = {
    kind.value.lower(): kind for kind in SourceKind
} | {
    kind.name.lower(): kind for kind in SourceKind
}


@dataclass
class FileCommandIngress:
    """
    Convert JSON command files into JobRequest objects.

    This class is intentionally narrow:
        - it watches a directory
        - parses JSON command files
        - submits JobRequest objects to ScanJobQueue
        - moves accepted command files to processed/
        - moves bad command files to failed/

    It does not execute ToS actions.
    """

    command_root: Path
    logger: logging.Logger | None = None

    incoming_name: str = "incoming"
    processing_name: str = "processing"
    processed_name: str = "processed"
    failed_name: str = "failed"

    def __post_init__(self) -> None:
        self.command_root = Path(self.command_root)

        self.incoming_dir = self.command_root / self.incoming_name
        self.processing_dir = self.command_root / self.processing_name
        self.processed_dir = self.command_root / self.processed_name
        self.failed_dir = self.command_root / self.failed_name

    def ensure_directories(self) -> None:
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.processing_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    def add_pending_jobs(
        self,
        job_queue: ScanJobQueue,
        *,
        max_files: int = 25,
    ) -> int:
        """
        Process up to max_files command files.

        Returns the number of command files successfully accepted and submitted
        to the job queue.
        """

        self.ensure_directories()

        accepted_count = 0

        command_files = sorted(self.incoming_dir.glob("*.json"))

        for incoming_path in command_files[:max_files]:
            try:
                processing_path = self._move_to_processing(incoming_path)

            except PermissionError as exc:
                self._log_info(
                    "Command file temporarily unavailable; "
                    "will retry on a later poll: %s (%s)",
                    incoming_path.name,
                    exc,
                )
                continue

            except FileNotFoundError:
                # Another process may have moved the file after the directory scan.
                continue

            try:
                request = self._parse_command_file(processing_path)

                job_queue.submit(request)

                self._move_to_processed(processing_path)

                accepted_count += 1
                self._log_info(
                    "Accepted command file %s as job %s",
                    incoming_path.name,
                    request.kind.value,
                )

            except Exception as exc:
                self._log_error(
                    "Failed to process command file %s: %s",
                    incoming_path.name,
                    exc,
                )

                if processing_path.exists():
                    self._move_to_failed(processing_path, exc)

        return accepted_count

    def _move_to_processing(self, incoming_path: Path) -> Path:
        """
        Move a command from incoming/ to processing/.

        If another process is still writing the file, this move may fail.
        That is okay; the scanner will try again on a later poll.
        """

        target_path = self._unique_path(self.processing_dir / incoming_path.name)
        incoming_path.replace(target_path)
        return target_path

    def _move_to_processed(self, processing_path: Path) -> Path:
        target_path = self._unique_path(self.processed_dir / processing_path.name)
        processing_path.replace(target_path)
        return target_path

    def _move_to_failed(self, path: Path, exc: Exception) -> Path:
        target_path = self._unique_path(self.failed_dir / path.name)
        path.replace(target_path)

        error_path = target_path.with_suffix(target_path.suffix + ".error.txt")
        error_path.write_text(str(exc), encoding="utf-8")

        return target_path

    def _parse_command_file(self, path: Path) -> JobRequest:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandParseError(f"Invalid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise CommandParseError("Command file must contain a JSON object.")

        return self._payload_to_job_request(payload, command_file=path)

    def _payload_to_job_request(
        self,
        payload: dict[str, Any],
        *,
        command_file: Path,
    ) -> JobRequest:
        command_text = self._required_text(payload, "command")
        kind = self._parse_job_kind(command_text)

        command_id = self._optional_text(payload, "command_id") or command_file.stem
        requested_at = self._parse_requested_at(payload)

        source = self._parse_optional_source(payload.get("source"))

        symbols = self._parse_symbols(payload.get("symbols"))

        symbol_file = self._parse_optional_path(payload.get("symbol_file"))
        output_dir = self._parse_optional_path(payload.get("output_dir"))

        target_filename = self._optional_text(payload, "target_filename")

        self._validate_request_fields(
            kind=kind,
            symbols=symbols,
            symbol_file=symbol_file,
        )

        return JobRequest(
            kind=kind,
            origin=JobOrigin.FILE_COMMAND,
            requested_at=requested_at,
            source=source,
            symbols=symbols,
            symbol_file=symbol_file,
            output_dir=output_dir,
            target_filename=target_filename,
            command_id=command_id,
            raw_payload=payload,
        )

    def _validate_request_fields(
        self,
        *,
        kind: JobKind,
        symbols: list[str],
        symbol_file: Path | None,
    ) -> None:
        if kind in {JobKind.REPLACE_WL_SYMBOLS, JobKind.ADD_WL_SYMBOLS}:
            if not symbols and symbol_file is None:
                raise CommandParseError(
                    f"{kind.value} requires either 'symbols' or 'symbol_file'."
                )

    def _parse_job_kind(self, value: str) -> JobKind:
        key = value.strip().lower()

        try:
            return _JOB_KIND_BY_TEXT[key]
        except KeyError as exc:
            valid = ", ".join(kind.value for kind in JobKind)
            raise CommandParseError(
                f"Unknown command {value!r}. Valid commands: {valid}"
            ) from exc

    def _parse_optional_source(self, value: Any) -> SourceKind | None:
        if value is None:
            return None

        if not isinstance(value, str):
            raise CommandParseError("'source' must be a string if provided.")

        key = value.strip().lower()

        try:
            return _SOURCE_KIND_BY_TEXT[key]
        except KeyError as exc:
            valid = ", ".join(kind.value for kind in SourceKind)
            raise CommandParseError(
                f"Unknown source {value!r}. Valid sources: {valid}"
            ) from exc

    def _parse_symbols(self, value: Any) -> list[str]:
        if value is None:
            return []

        if isinstance(value, str):
            raw_symbols = value.replace(",", " ").split()
        elif isinstance(value, list):
            raw_symbols = value
        else:
            raise CommandParseError("'symbols' must be a string or list of strings.")

        symbols: list[str] = []

        for item in raw_symbols:
            if not isinstance(item, str):
                raise CommandParseError("'symbols' list must contain only strings.")

            symbol = item.strip().upper()

            if symbol:
                symbols.append(symbol)

        return symbols

    def _parse_optional_path(self, value: Any) -> Path | None:
        if value is None:
            return None

        if not isinstance(value, str):
            raise CommandParseError("Path values must be strings.")

        raw_path = value.strip()

        if not raw_path:
            return None

        path = Path(raw_path)

        if path.is_absolute():
            return path

        return self.command_root / path

    def _parse_requested_at(self, payload: dict[str, Any]) -> datetime:
        value = payload.get("requested_at")

        if value is None:
            return datetime.now()

        if not isinstance(value, str):
            raise CommandParseError("'requested_at' must be an ISO datetime string.")

        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise CommandParseError(
                f"Could not parse requested_at datetime: {value!r}"
            ) from exc

    def _required_text(self, payload: dict[str, Any], key: str) -> str:
        value = payload.get(key)

        if not isinstance(value, str) or not value.strip():
            raise CommandParseError(f"Missing required string field: {key!r}")

        return value.strip()

    def _optional_text(self, payload: dict[str, Any], key: str) -> str | None:
        value = payload.get(key)

        if value is None:
            return None

        if not isinstance(value, str):
            raise CommandParseError(f"{key!r} must be a string if provided.")

        value = value.strip()

        return value or None

    def _unique_path(self, path: Path) -> Path:
        if not path.exists():
            return path

        stem = path.stem
        suffix = path.suffix
        parent = path.parent

        for index in range(1, 10_000):
            candidate = parent / f"{stem}-{index:04d}{suffix}"

            if not candidate.exists():
                return candidate

        raise RuntimeError(f"Could not create unique path for {path}")

    def _log_info(self, msg: str, *args: Any) -> None:
        if self.logger is not None:
            self.logger.info(msg, *args)

    def _log_error(self, msg: str, *args: Any) -> None:
        if self.logger is not None:
            self.logger.error(msg, *args)
            
