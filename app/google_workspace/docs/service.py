"""Bounded structural Google Docs reads with no mutation surface."""

from __future__ import annotations

import re
from typing import Any

from app.google_workspace.docs.dtos import DocumentContent
from app.google_workspace.docs.exceptions import (
    DocsAuthenticationError, DocsInvalidRequestError, DocsNetworkError, DocsNotFoundError,
    DocsPermissionError, DocsProviderError, DocsRateLimitError, DocsServiceError,
)
from app.google_workspace.factory import GoogleClientFactory

_MAX_PARAGRAPHS = 200
_MAX_PARAGRAPH_LENGTH = 1_000


class DocsService:
    """Expose a bounded plain-text paragraph walk for one document."""

    def __init__(self, client_factory: GoogleClientFactory) -> None:
        self._client_factory = client_factory

    def get_document(self, file_id: str) -> DocumentContent:
        if not isinstance(file_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", file_id):
            raise DocsInvalidRequestError()
        try:
            raw = self._client_factory.get_service("docs", "v1").documents().get(documentId=file_id).execute()
            if not isinstance(raw, dict):
                raise DocsProviderError()
            paragraphs: list[str] = []
            truncated = False
            body = raw.get("body", {})
            content = body.get("content", []) if isinstance(body, dict) else []
            if not isinstance(content, list):
                raise DocsProviderError()
            for element in content:
                paragraph = element.get("paragraph") if isinstance(element, dict) else None
                elements = paragraph.get("elements", []) if isinstance(paragraph, dict) else []
                text = "".join(
                    run.get("content", "")
                    for item in elements if isinstance(item, dict)
                    for run in [item.get("textRun", {})]
                    if isinstance(run, dict) and isinstance(run.get("content"), str)
                ).strip()
                if text:
                    if len(paragraphs) >= _MAX_PARAGRAPHS:
                        truncated = True
                        break
                    paragraphs.append(text[:_MAX_PARAGRAPH_LENGTH])
                    truncated = truncated or len(text) > _MAX_PARAGRAPH_LENGTH
            title = raw.get("title")
            return DocumentContent(file_id, title[:200] if isinstance(title, str) else "", tuple(paragraphs), truncated)
        except DocsServiceError:
            raise
        except Exception as error:
            raise self._map_error(error) from None

    @staticmethod
    def _map_error(error: Exception) -> DocsServiceError:
        status = getattr(error, "status_code", None) or getattr(getattr(error, "resp", None), "status", None)
        if status == 401: return DocsAuthenticationError()
        if status == 403: return DocsPermissionError()
        if status == 404: return DocsNotFoundError()
        if status == 429: return DocsRateLimitError()
        if status == 400: return DocsInvalidRequestError()
        if isinstance(error, (TimeoutError, ConnectionError, OSError)): return DocsNetworkError()
        return DocsProviderError()
