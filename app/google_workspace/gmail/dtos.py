"""Privacy-preserving DTOs returned by the read-only Gmail service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MessageSummary:
    message_id: str
    thread_id: str
    subject: str
    sender_alias: str
    received_at: datetime
    snippet: str
    has_attachments: bool
    label_ids: tuple[str, ...]


@dataclass(frozen=True)
class ThreadSummary:
    thread_id: str
    message_count: int
    messages: tuple[MessageSummary, ...]


@dataclass(frozen=True)
class MessageSearchResult:
    messages: tuple[MessageSummary, ...]
    truncated: bool
