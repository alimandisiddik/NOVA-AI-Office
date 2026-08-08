"""Deterministic, bounded composition over the read-only Workspace services."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Protocol

from app.google_workspace.calendar.dtos import CalendarEvent, Conflict, PaginationResult, TimeRange
from app.google_workspace.gmail.dtos import MessageSummary
from app.workspace_intel.models import AgendaView, FlaggedMeeting, PrioritizedInboxView, PrioritizedMessage

_MAX_INBOX_LIMIT = 50
_MAX_AGENDA_DAYS = 2
_BULK_CATEGORY_LABELS = ("CATEGORY_PROMOTIONS", "CATEGORY_FORUMS", "CATEGORY_UPDATES")


class _GmailReader(Protocol):
    def search_messages(self, query: str, max_results: int = 20) -> object: ...


class _CalendarReader(Protocol):
    def list_today(self, now: datetime | None = None) -> PaginationResult: ...

    def detect_conflicts(self, window: TimeRange) -> tuple[Conflict, ...]: ...

    def build_meeting_brief(self, event: CalendarEvent) -> object: ...


class WorkspaceIntelService:
    """Compose fresh, read-only Gmail and Calendar intelligence views."""

    def __init__(
        self,
        gmail: _GmailReader,
        calendar: _CalendarReader,
        summarizer: Callable[[str], str] | None = None,
        deadline_sources: object | None = None,
    ) -> None:
        self._gmail = gmail
        self._calendar = calendar
        self._summarizer = summarizer
        self._deadline_sources = deadline_sources

    def prioritized_inbox(self, limit: int = 20) -> PrioritizedInboxView:
        """Return a sorted, metadata-only inbox view from a bounded Gmail search."""
        bounded_limit = self._inbox_limit(limit)
        result = self._gmail.search_messages("in:inbox", max_results=bounded_limit)
        messages = tuple(getattr(result, "messages", ()))
        prioritized = sorted(
            (self._prioritize_message(message) for message in messages),
            key=lambda item: (-item.priority_score, -item.message.received_at.timestamp(), item.message.message_id),
        )
        return PrioritizedInboxView(
            generated_at=self._generated_at(),
            messages=tuple(prioritized[:bounded_limit]),
            truncated=bool(getattr(result, "truncated", False)) or len(messages) > bounded_limit,
        )

    def upcoming_agenda(self, days: int = 2) -> AgendaView:
        """Return today and tomorrow's events with existing conflict/preparation signals."""
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= _MAX_AGENDA_DAYS:
            raise ValueError("days must be between 1 and 2")

        today_result = self._calendar.list_today()
        today_events = tuple(today_result.events)
        tomorrow_events: tuple[CalendarEvent, ...] = ()
        truncated = bool(getattr(today_result, "truncated", False))
        if days == 2:
            # A fixed Mon-Sun list_week() window silently drops "tomorrow" when
            # today is Sunday; list_today(now=...) has no such boundary case.
            tomorrow_moment = datetime.now(timezone.utc) + timedelta(days=1)
            tomorrow_result = self._calendar.list_today(now=tomorrow_moment)
            tomorrow_events = tuple(tomorrow_result.events)
            truncated = truncated or bool(getattr(tomorrow_result, "truncated", False))
        events = self._unique_events((*today_events, *tomorrow_events))
        conflict_ids = self._conflict_ids(events)

        today = tuple(self._flag_meeting(event, conflict_ids) for event in today_events)
        tomorrow = tuple(self._flag_meeting(event, conflict_ids) for event in tomorrow_events)
        return AgendaView(generated_at=self._generated_at(), today=today, tomorrow=tomorrow, truncated=truncated)

    def deadlines_and_invitations(self, days: int = 2) -> AgendaView:
        """Forward-compatible alias for upcoming_agenda(). The documented
        attendee_count>0-and-not-organizer invitation filter is not yet
        implementable: 8A's CalendarEvent exposes no "is this account the
        organizer" signal to compare organizer_alias against, and adding
        one is an app/google_workspace/** change outside 8B's scope. The
        optional deadline_sources (8D) hook stays inert until Stage 3."""
        return self.upcoming_agenda(days=days)

    @staticmethod
    def _inbox_limit(limit: int) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _MAX_INBOX_LIMIT:
            raise ValueError("limit must be between 1 and 50")
        return limit

    def _prioritize_message(self, message: MessageSummary) -> PrioritizedMessage:
        score = 0
        signals: list[str] = []
        labels = {label.upper() for label in message.label_ids}
        if "UNREAD" in labels:
            score += 35
            signals.append("it remains unread")
        age = datetime.now(timezone.utc) - message.received_at.astimezone(timezone.utc)
        if age <= timedelta(days=2):
            score += 30
            signals.append("it was received recently")
        elif age <= timedelta(days=7):
            score += 15
            signals.append("it was received this week")
        if not message.subject.lower().lstrip().startswith("re:"):
            score += 15
            signals.append("the subject has no prior-reply marker")
        if self._is_bulk_sender(labels):
            signals.append("Gmail has categorized this message as promotional/forum/update mail")
        else:
            score += 20
            signals.append("Gmail has not categorized this message as bulk mail")

        reason = (
            "Likely needs attention because " + ", ".join(signals) + "."
            if signals
            else "May need review; the available metadata provides limited attention signals."
        )
        recommendation = "Prepare a reply" if score >= 50 else "Review when convenient"
        return PrioritizedMessage(message=message, priority_score=min(score, 100), reason=reason, recommendation=recommendation)

    def _flag_meeting(self, event: CalendarEvent, conflict_ids: set[str]) -> FlaggedMeeting:
        brief = self._calendar.build_meeting_brief(event)
        return FlaggedMeeting(
            brief=brief,
            has_conflict=event.id in conflict_ids,
            missing_preparation=getattr(brief, "project_reference", None) is None,
        )

    def _conflict_ids(self, events: tuple[CalendarEvent, ...]) -> set[str]:
        """Reuse CalendarService conflict detection for each bounded event window."""
        conflict_ids: set[str] = set()
        for event in events:
            for conflict in self._calendar.detect_conflicts(event.time_range):
                if conflict.event_id != event.id:
                    conflict_ids.add(event.id)
                    conflict_ids.add(conflict.event_id)
        return conflict_ids

    @staticmethod
    def _is_bulk_sender(labels: set[str]) -> bool:
        # sender_alias is a privacy-scrubbed hash (8A), not pattern-matchable text.
        return bool(labels & set(_BULK_CATEGORY_LABELS))

    @staticmethod
    def _unique_events(events: Iterable[CalendarEvent]) -> tuple[CalendarEvent, ...]:
        unique: dict[str, CalendarEvent] = {}
        for event in events:
            unique.setdefault(event.id, event)
        return tuple(unique.values())

    @staticmethod
    def _generated_at() -> str:
        return datetime.now(timezone.utc).isoformat()
