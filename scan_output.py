# scan_output.py

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from scan_jobs import SourceKind


def timestamp_for_filename(when: datetime | None = None) -> str:
    if when is None:
        when = datetime.now()

    return when.strftime("%Y-%m-%d-%H-%M-%S")


def build_scan_filename(
    *,
    source: SourceKind,
    when: datetime | None = None,
    suffix: str = ".csv",
) -> str:
    stamp = timestamp_for_filename(when)
    return f"{stamp}-{source.value}{suffix}"


def build_output_path(
    *,
    output_dir: Path,
    source: SourceKind,
    when: datetime | None = None,
    target_filename: str | None = None,
) -> Path:
    output_dir = Path(output_dir)

    if target_filename:
        return output_dir / target_filename

    return output_dir / build_scan_filename(
        source=source,
        when=when,
    )
