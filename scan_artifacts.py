# scan_artifacts.py

"""Scan artifact naming helpers for CSV scan outputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

SOURCE_TOS_SCAN = "ToS-scan"
SOURCE_TOS_MANUAL = "ToS-manual"
SOURCE_WATCHLIST = "Watchlist"
SOURCE_SCHWAB_API = "Schwab-API"
SOURCE_MASSIVE_API = "Massive-API"


SCAN_SOURCE_CODES: dict[str, str] = {
    SOURCE_TOS_SCAN: "TS",
    SOURCE_TOS_MANUAL: "TM",
    SOURCE_WATCHLIST: "WL",
    SOURCE_SCHWAB_API: "SA",
    SOURCE_MASSIVE_API: "MA",
}


@dataclass(frozen=True)
class ScanArtifactSpec:
    """Specification for a generated scan artifact filename."""

    source_name: str
    extension: str = ".csv"

    @property
    def source_code(self) -> str:
        try:
            return SCAN_SOURCE_CODES[self.source_name]
        except KeyError as exc:
            known = ", ".join(sorted(SCAN_SOURCE_CODES))
            raise ValueError(
                f"Unknown scan source {self.source_name!r}. "
                f"Known sources: {known}"
            ) from exc

    def filename_for(self, dt: datetime) -> str:
        stamp = dt.strftime("%Y-%m-%d-%H-%M-%S")
        return f"{stamp}-{self.source_code}{self.extension}"
