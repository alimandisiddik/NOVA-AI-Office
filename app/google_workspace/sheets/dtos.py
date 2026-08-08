"""Safe Google Sheets read DTOs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpreadsheetMetadata:
    file_id: str
    title: str
    sheet_titles: tuple[str, ...]


@dataclass(frozen=True)
class RangeValues:
    file_id: str
    a1_range: str
    rows: tuple[tuple[str, ...], ...]
