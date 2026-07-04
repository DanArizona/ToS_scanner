from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class SourceKind(str, Enum):
    TS = "TS"
    TM = "TM"
    WL = "WL"
    SA = "SA"
    MA = "MA"


class JobKind(str, Enum):
    EXPORT_TS = "export_ts"
    EXPORT_TM = "export_tm"
    EXPORT_WL = "export_wl"
    REPLACE_WL_SYMBOLS = "replace_wl_symbols"
    ADD_WL_SYMBOLS = "add_wl_symbols"
    START = "start"
    STOP = "stop"
    PAUSE = "pause"
    RESUME = "resume"


class JobOrigin(str, Enum):
    SCHEDULER = "scheduler"
    GUI = "gui"
    FILE_COMMAND = "file_command"
    HTTP = "http"
    SOCKET = "socket"


@dataclass(frozen=True)
class JobRequest:
    kind: JobKind
    origin: JobOrigin
    requested_at: datetime

    source: SourceKind | None = None

    symbols: list[str] = field(default_factory=list)
    symbol_file: Path | None = None

    output_dir: Path | None = None
    target_filename: str | None = None

    command_id: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobResult:
    request: JobRequest
    ok: bool
    message: str
    output_path: Path | None = None
    error: str | None = None

    