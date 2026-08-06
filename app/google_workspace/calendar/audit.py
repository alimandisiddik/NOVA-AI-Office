"""Safe audit sink for Calendar operations without provider-content storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


@dataclass(frozen=True)
class CalendarAuditRecord:
    timestamp: datetime
    operation: str
    calendar_hash: str
    result_count: int
    duration_ms: int
    success: bool
    category: str
    correlation_id: str | None = None


class CalendarAuditSink(Protocol):
    def record(self, record: CalendarAuditRecord) -> None: ...


class InMemoryCalendarAuditSink:
    """Immutable in-process audit trail suitable for Sprint 5D integration wiring."""

    def __init__(self) -> None:
        self._records: list[CalendarAuditRecord] = []

    def record(self, record: CalendarAuditRecord) -> None:
        if record.timestamp.tzinfo is None or record.timestamp.utcoffset() is None:
            raise ValueError("Audit timestamps must be timezone-aware")
        self._records.append(record)

    def records(self) -> tuple[CalendarAuditRecord, ...]:
        return tuple(self._records)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
