"""Safe Google Slides read DTOs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlideContent:
    slide_index: int
    text_fragments: tuple[str, ...]


@dataclass(frozen=True)
class PresentationContent:
    file_id: str
    title: str
    slides: tuple[SlideContent, ...]
    truncated: bool
