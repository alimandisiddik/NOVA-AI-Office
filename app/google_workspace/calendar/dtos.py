"""Immutable, privacy-safe DTOs for the Calendar read integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class TimeRange:
    """A non-empty interval with timezone-aware endpoints."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _require_aware(self.start, "start")
        _require_aware(self.end, "end")
        if self.end <= self.start:
            raise ValueError("end must be later than start")


@dataclass(frozen=True)
class CalendarEvent:
    """Sanitized provider event metadata; never includes descriptions or attendees."""

    id: str
    title: str
    time_range: TimeRange
    timezone: str
    all_day: bool = False
    location: Optional[str] = None
    attendee_count: int = 0
    organizer_alias: Optional[str] = None
    conference_provider: Optional[str] = None
    recurring_event_id: Optional[str] = None

    @property
    def start(self) -> datetime:
        return self.time_range.start

    @property
    def end(self) -> datetime:
        return self.time_range.end

    def __post_init__(self) -> None:
        if not self.id or not self.title or not self.timezone:
            raise ValueError("event id, title, and timezone are required")
        if self.attendee_count < 0:
            raise ValueError("attendee_count cannot be negative")


@dataclass(frozen=True)
class MeetingBrief:
    """Limited event metadata for Control Tower meeting summaries."""

    title: str
    time_range: TimeRange
    timezone: str
    location: Optional[str] = None
    attendee_count: int = 0
    organizer_alias: Optional[str] = None
    conference_provider: Optional[str] = None
    project_reference: Optional[str] = None

    @property
    def start(self) -> datetime:
        return self.time_range.start

    @property
    def end(self) -> datetime:
        return self.time_range.end


@dataclass(frozen=True)
class EventDraft:
    """A validated local-only draft. It has no provider mutation capability."""

    title: str
    time_range: TimeRange
    timezone: str
    all_day: bool = False
    location: Optional[str] = None

    @property
    def start(self) -> datetime:
        return self.time_range.start

    @property
    def end(self) -> datetime:
        return self.time_range.end


@dataclass(frozen=True)
class FreeBusyCalendarError:
    """Safe error classification for one requested calendar alias."""

    calendar_alias: str
    category: str


@dataclass(frozen=True)
class FreeBusyResult:
    time_range: TimeRange
    busy_slots: tuple[TimeRange, ...] = ()
    calendar_errors: tuple[FreeBusyCalendarError, ...] = ()


@dataclass(frozen=True)
class Conflict:
    """A deterministic, unique conflict between a range and an event."""

    event_id: str
    event_title: str
    overlap: TimeRange


@dataclass(frozen=True)
class PaginationResult:
    """A bounded result set without raw Google pagination tokens."""

    events: tuple[CalendarEvent, ...] = ()
    truncated: bool = False
