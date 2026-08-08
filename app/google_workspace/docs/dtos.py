"""Safe Google Docs read DTOs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentContent:
    file_id: str
    title: str
    paragraphs: tuple[str, ...]
    truncated: bool
