"""Non-sensitive Gmail read audit records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class GmailAuditRecord:
    timestamp: datetime
    operation: str
    result_count: int
    success: bool
    category: str
    correlation_id: str | None


class GmailAuditSink(Protocol):
    def record(self, record: GmailAuditRecord) -> None: ...


class NullGmailAuditSink:
    def record(self, record: GmailAuditRecord) -> None:
        return None
