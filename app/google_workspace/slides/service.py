"""Bounded structural Google Slides reads with no mutation surface."""

from __future__ import annotations

import re
from typing import Any

from app.google_workspace.factory import GoogleClientFactory
from app.google_workspace.slides.dtos import PresentationContent, SlideContent
from app.google_workspace.slides.exceptions import (
    SlidesAuthenticationError, SlidesInvalidRequestError, SlidesNetworkError, SlidesNotFoundError,
    SlidesPermissionError, SlidesProviderError, SlidesRateLimitError, SlidesServiceError,
)

_MAX_SLIDES = 100
_MAX_FRAGMENTS = 100
_MAX_FRAGMENT_LENGTH = 1_000


class SlidesService:
    """Expose a bounded plain-text structural walk for one presentation."""

    def __init__(self, client_factory: GoogleClientFactory) -> None:
        self._client_factory = client_factory

    def get_presentation(self, file_id: str) -> PresentationContent:
        if not isinstance(file_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", file_id):
            raise SlidesInvalidRequestError()
        try:
            raw = self._client_factory.get_service("slides", "v1").presentations().get(presentationId=file_id).execute()
            if not isinstance(raw, dict): raise SlidesProviderError()
            raw_slides = raw.get("slides", [])
            if not isinstance(raw_slides, list): raise SlidesProviderError()
            slides: list[SlideContent] = []
            truncated = len(raw_slides) > _MAX_SLIDES
            for index, slide in enumerate(raw_slides[:_MAX_SLIDES]):
                fragments: list[str] = []
                page_elements = slide.get("pageElements", []) if isinstance(slide, dict) else []
                for element in page_elements if isinstance(page_elements, list) else []:
                    shape = element.get("shape", {}) if isinstance(element, dict) else {}
                    text_elements = shape.get("text", {}).get("textElements", []) if isinstance(shape, dict) and isinstance(shape.get("text"), dict) else []
                    for text_element in text_elements if isinstance(text_elements, list) else []:
                        content = text_element.get("textRun", {}).get("content") if isinstance(text_element, dict) and isinstance(text_element.get("textRun"), dict) else None
                        if isinstance(content, str) and content.strip():
                            if len(fragments) >= _MAX_FRAGMENTS:
                                truncated = True
                                break
                            fragments.append(content.strip()[:_MAX_FRAGMENT_LENGTH])
                            truncated = truncated or len(content.strip()) > _MAX_FRAGMENT_LENGTH
                slides.append(SlideContent(index, tuple(fragments)))
            return PresentationContent(file_id, str(raw.get("title", ""))[:200], tuple(slides), truncated)
        except SlidesServiceError: raise
        except Exception as error: raise self._map_error(error) from None

    @staticmethod
    def _map_error(error: Exception) -> SlidesServiceError:
        status = getattr(error, "status_code", None) or getattr(getattr(error, "resp", None), "status", None)
        if status == 401: return SlidesAuthenticationError()
        if status == 403: return SlidesPermissionError()
        if status == 404: return SlidesNotFoundError()
        if status == 429: return SlidesRateLimitError()
        if status == 400: return SlidesInvalidRequestError()
        if isinstance(error, (TimeoutError, ConnectionError, OSError)): return SlidesNetworkError()
        return SlidesProviderError()
