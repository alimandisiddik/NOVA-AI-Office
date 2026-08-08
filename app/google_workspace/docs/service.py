"""Bounded structural Google Docs reads with no mutation surface."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.google_workspace.docs.dtos import CreatedDocument, DocumentContent
from app.google_workspace.docs.exceptions import (
    DocsAuthenticationError, DocsInvalidRequestError, DocsNetworkError, DocsNotFoundError,
    DocsPermissionError, DocsProviderError, DocsRateLimitError, DocsServiceError, DocsSubmissionUncertainError,
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


class DocsWriteService:
    """Typed, private Docs creation boundary; no sharing or generic execution."""

    def __init__(self, client_factory: GoogleClientFactory) -> None:
        self._client_factory = client_factory

    def create_private_document(self, title: str, body_text: str) -> CreatedDocument:
        if not isinstance(title, str) or not title.strip() or len(title) > 200 or not isinstance(body_text, str):
            raise DocsInvalidRequestError()
        try:
            docs = self._client_factory.get_service("docs", "v1").documents()
            try:
                created = docs.create(body={"title": title.strip()}).execute()
            except Exception as error:
                # A network/transport failure on the create call itself is
                # exactly the ambiguous case the frozen contract calls out:
                # the request may have reached Google and created an orphan
                # document before the response was lost. Only a definite
                # server response (auth/permission/rate-limit/invalid
                # rejection) is safe to treat as "no write happened".
                mapped = DocsService._map_error(error)
                if isinstance(mapped, DocsNetworkError):
                    raise DocsSubmissionUncertainError() from error
                raise mapped from None
            document_id = created.get("documentId") if isinstance(created, dict) else None
            if not isinstance(document_id, str) or not document_id:
                raise DocsProviderError()
            try:
                docs.batchUpdate(documentId=document_id, body={"requests": [{"insertText": {"location": {"index": 1}, "text": body_text}}]}).execute()
            except Exception as error:
                raise DocsSubmissionUncertainError() from error
            return CreatedDocument(document_id, title.strip())
        except DocsServiceError:
            raise
        except Exception as error:
            raise DocsService._map_error(error) from None


class LazyDocsWriteService:
    """Defers credentials and client resolution until an approved write claim."""

    def __init__(self, factory_supplier: Callable[[], GoogleClientFactory]) -> None:
        self._factory_supplier = factory_supplier

    def create_private_document(self, title: str, body_text: str) -> CreatedDocument:
        return DocsWriteService(self._factory_supplier()).create_private_document(title, body_text)
