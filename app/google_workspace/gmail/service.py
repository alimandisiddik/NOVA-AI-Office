"""Bounded, privacy-preserving Gmail reads with no mutation surface."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from app.google_workspace.factory import GoogleClientFactory
from app.google_workspace.gmail.audit import GmailAuditRecord, GmailAuditSink, NullGmailAuditSink
from app.google_workspace.gmail.dtos import MessageSearchResult, MessageSummary, ThreadSummary
from app.google_workspace.gmail.exceptions import (
    GmailAuthenticationError,
    GmailInvalidRequestError,
    GmailNetworkError,
    GmailNotFoundError,
    GmailPermissionError,
    GmailProviderError,
    GmailRateLimitError,
    GmailServiceError,
)

_SAFE_QUERY = re.compile(r"^[\w\s.,:@/&()\-']{1,120}$", re.UNICODE)
_MAX_RESULTS = 50
_MAX_SNIPPET_LENGTH = 200


class GmailService:
    """Expose only bounded Gmail metadata and snippets."""

    def __init__(self, client_factory: GoogleClientFactory, audit_sink: GmailAuditSink | None = None) -> None:
        self._client_factory = client_factory
        self._audit_sink = audit_sink or NullGmailAuditSink()

    def search_messages(
        self, query: str, max_results: int = 20, correlation_id: str | None = None
    ) -> MessageSearchResult:
        if not isinstance(query, str) or not _SAFE_QUERY.fullmatch(query.strip()):
            raise GmailInvalidRequestError()
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= _MAX_RESULTS:
            raise GmailInvalidRequestError()
        try:
            service = self._client_factory.get_service("gmail", "v1")
            listing = service.users().messages().list(userId="me", q=query.strip(), maxResults=max_results).execute()
            raw_messages = listing.get("messages", []) if isinstance(listing, dict) else []
            if not isinstance(raw_messages, list):
                raise GmailProviderError()
            summaries = tuple(
                self._message_summary(
                    service.users().messages().get(
                        userId="me", id=message_id, format="metadata", metadataHeaders=["Subject", "From", "Date"]
                    ).execute()
                )
                for item in raw_messages[:max_results]
                if isinstance(item, dict) and isinstance((message_id := item.get("id")), str)
            )
            self._audit("search_messages", len(summaries), True, "success", correlation_id)
            return MessageSearchResult(summaries, bool(listing.get("nextPageToken")) or len(raw_messages) > max_results)
        except GmailServiceError as error:
            self._audit("search_messages", 0, False, error.category, correlation_id)
            raise
        except Exception as error:
            mapped = self._map_error(error)
            self._audit("search_messages", 0, False, mapped.category, correlation_id)
            raise mapped from None

    def get_message_metadata(self, message_id: str) -> MessageSummary:
        if not self._valid_identifier(message_id):
            raise GmailInvalidRequestError()
        try:
            service = self._client_factory.get_service("gmail", "v1")
            return self._message_summary(
                service.users().messages().get(
                    userId="me", id=message_id, format="metadata", metadataHeaders=["Subject", "From", "Date"]
                ).execute()
            )
        except GmailServiceError:
            raise
        except Exception as error:
            raise self._map_error(error) from None

    def list_thread(self, thread_id: str) -> ThreadSummary:
        if not self._valid_identifier(thread_id):
            raise GmailInvalidRequestError()
        try:
            service = self._client_factory.get_service("gmail", "v1")
            raw_thread = service.users().threads().get(
                userId="me", id=thread_id, format="metadata", metadataHeaders=["Subject", "From", "Date"]
            ).execute()
            messages = raw_thread.get("messages", []) if isinstance(raw_thread, dict) else []
            if not isinstance(messages, list):
                raise GmailProviderError()
            return ThreadSummary(thread_id, len(messages), tuple(self._message_summary(raw) for raw in messages[:_MAX_RESULTS]))
        except GmailServiceError:
            raise
        except Exception as error:
            raise self._map_error(error) from None

    @staticmethod
    def _valid_identifier(value: object) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value))

    @staticmethod
    def _message_summary(raw: object) -> MessageSummary:
        if not isinstance(raw, dict):
            raise GmailProviderError()
        message_id = raw.get("id")
        thread_id = raw.get("threadId")
        if not isinstance(message_id, str) or not isinstance(thread_id, str):
            raise GmailProviderError()
        headers = raw.get("payload", {}).get("headers", []) if isinstance(raw.get("payload"), dict) else []
        header_map = {
            item.get("name", "").lower(): item.get("value", "")
            for item in headers if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("value"), str)
        }
        sender = header_map.get("from", "")
        try:
            received_at = parsedate_to_datetime(header_map.get("date", "")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            received_at = datetime.fromtimestamp(0, tz=timezone.utc)
        parts = raw.get("payload", {}).get("parts", []) if isinstance(raw.get("payload"), dict) else []
        return MessageSummary(
            message_id=message_id,
            thread_id=thread_id,
            subject=header_map.get("subject", "")[:200],
            sender_alias="sender_" + hashlib.sha256(sender.encode("utf-8")).hexdigest()[:12],
            received_at=received_at,
            snippet=str(raw.get("snippet", ""))[:_MAX_SNIPPET_LENGTH],
            has_attachments=any(isinstance(part, dict) and part.get("filename") for part in parts),
            label_ids=tuple(label for label in raw.get("labelIds", []) if isinstance(label, str))[:20],
        )

    @staticmethod
    def _map_error(error: Exception) -> GmailServiceError:
        status = getattr(error, "status_code", None) or getattr(getattr(error, "resp", None), "status", None)
        if status == 401:
            return GmailAuthenticationError()
        if status == 403:
            return GmailPermissionError()
        if status == 404:
            return GmailNotFoundError()
        if status == 429:
            return GmailRateLimitError()
        if status == 400:
            return GmailInvalidRequestError()
        if isinstance(error, (TimeoutError, ConnectionError, OSError)):
            return GmailNetworkError()
        return GmailProviderError()

    def _audit(self, operation: str, result_count: int, success: bool, category: str, correlation_id: str | None) -> None:
        try:
            self._audit_sink.record(
                GmailAuditRecord(datetime.now(timezone.utc), operation, result_count, success, category, correlation_id)
            )
        except Exception:
            return None
