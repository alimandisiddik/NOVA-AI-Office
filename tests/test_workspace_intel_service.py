"""Focused contract tests for read-only Workspace intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import inspect

import pytest

import app.workspace_intel.service as workspace_intel_service
from app.google_workspace.calendar.dtos import CalendarEvent, Conflict, MeetingBrief, PaginationResult, TimeRange
from app.google_workspace.gmail.dtos import MessageSearchResult, MessageSummary
from app.workspace_intel import WorkspaceIntelService


NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _message(
    message_id: str,
    *,
    unread: bool = True,
    subject: str = "Planning",
    days_old: int = 1,
    extra_labels: tuple[str, ...] = (),
) -> MessageSummary:
    base = ("INBOX", "UNREAD") if unread else ("INBOX",)
    return MessageSummary(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        subject=subject,
        sender_alias="sender_a1b2c3",
        received_at=NOW - timedelta(days=days_old),
        snippet="Privacy-safe snippet",
        has_attachments=False,
        label_ids=base + extra_labels,
    )


def _event(event_id: str, start: datetime, end: datetime) -> CalendarEvent:
    return CalendarEvent(event_id, f"Meeting {event_id}", TimeRange(start, end), "Asia/Jakarta", attendee_count=2)


@dataclass
class FakeGmail:
    result: MessageSearchResult
    calls: list[tuple[str, int]]

    def search_messages(self, query: str, max_results: int = 20) -> MessageSearchResult:
        self.calls.append((query, max_results))
        return self.result


@dataclass
class FakeCalendar:
    today: PaginationResult
    tomorrow: PaginationResult
    conflict_map: dict[tuple[datetime, datetime], tuple[Conflict, ...]]
    conflict_calls: list[TimeRange]
    list_today_calls: list[datetime | None] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.list_today_calls is None:
            self.list_today_calls = []

    def list_today(self, now: datetime | None = None) -> PaginationResult:
        self.list_today_calls.append(now)
        return self.tomorrow if now is not None else self.today

    def detect_conflicts(self, window: TimeRange) -> tuple[Conflict, ...]:
        self.conflict_calls.append(window)
        return self.conflict_map.get((window.start, window.end), ())

    @staticmethod
    def build_meeting_brief(event: CalendarEvent) -> MeetingBrief:
        return MeetingBrief(
            title=event.title,
            time_range=event.time_range,
            timezone=event.timezone,
            attendee_count=event.attendee_count,
        )


def _service(messages: tuple[MessageSummary, ...] = ()) -> tuple[WorkspaceIntelService, FakeGmail, FakeCalendar]:
    gmail = FakeGmail(MessageSearchResult(messages, False), [])
    calendar = FakeCalendar(PaginationResult(), PaginationResult(), {}, [])
    return WorkspaceIntelService(gmail, calendar), gmail, calendar


def test_prioritized_inbox_is_bounded_sorted_and_preserves_source_fact() -> None:
    low = _message("low", unread=False, subject="Re: Planning", days_old=9)
    high = _message("high", unread=True, days_old=1)
    gmail = FakeGmail(MessageSearchResult((low, high), True), [])
    calendar = FakeCalendar(PaginationResult(), PaginationResult(), {}, [])

    view = WorkspaceIntelService(gmail, calendar).prioritized_inbox(limit=1)

    assert gmail.calls == [("in:inbox", 1)]
    assert view.truncated is True
    assert len(view.messages) == 1
    prioritized = view.messages[0]
    assert prioritized.message is high
    assert prioritized.priority_score == 100
    assert prioritized.reason.startswith("Likely")
    assert "requires a response" not in prioritized.reason.lower()
    assert prioritized.recommendation == "Prepare a reply"


def test_prioritized_inbox_handles_empty_results_without_summarizer() -> None:
    service, _, _ = _service()

    view = service.prioritized_inbox()

    assert view.messages == ()
    assert view.truncated is False


def test_prioritized_inbox_rejects_unbounded_limit() -> None:
    service, _, _ = _service()

    with pytest.raises(ValueError):
        service.prioritized_inbox(limit=51)


def test_prioritized_inbox_bulk_category_label_withholds_bonus_not_penalized() -> None:
    """sender_alias is a SHA256 hash and can never contain a readable bulk
    marker; Gmail's own CATEGORY_PROMOTIONS/FORUMS/UPDATES labels are the
    only real FACT signal 8B can use for bulk-sender resistance."""
    bulk = _message("bulk", unread=True, days_old=1, extra_labels=("CATEGORY_PROMOTIONS",))

    view = WorkspaceIntelService(
        FakeGmail(MessageSearchResult((bulk,), False), []),
        FakeCalendar(PaginationResult(), PaginationResult(), {}, []),
    ).prioritized_inbox()

    prioritized = view.messages[0]
    assert prioritized.priority_score == 80  # unread(35) + recent(30) + no-Re:(15), no bulk bonus, never negative
    assert "promotional" in prioritized.reason.lower() or "forum" in prioritized.reason.lower()


def test_prioritized_inbox_non_bulk_label_receives_full_sender_signal() -> None:
    personal = _message("personal", unread=True, days_old=1)

    view = WorkspaceIntelService(
        FakeGmail(MessageSearchResult((personal,), False), []),
        FakeCalendar(PaginationResult(), PaginationResult(), {}, []),
    ).prioritized_inbox()

    assert view.messages[0].priority_score == 100


def test_upcoming_agenda_reuses_conflict_detection_and_flags_missing_preparation() -> None:
    jakarta = timezone(timedelta(hours=7))
    today_start = NOW.astimezone(jakarta).replace(hour=10, minute=0, second=0, microsecond=0)
    today_event = _event("today", today_start, today_start + timedelta(hours=1))
    tomorrow_start = today_start + timedelta(days=1)
    tomorrow_event = _event("tomorrow", tomorrow_start, tomorrow_start + timedelta(hours=1))
    conflict = Conflict("tomorrow", tomorrow_event.title, tomorrow_event.time_range)
    gmail = FakeGmail(MessageSearchResult((), False), [])
    calendar = FakeCalendar(
        PaginationResult((today_event,)),
        PaginationResult((tomorrow_event,)),
        {(today_event.start, today_event.end): (conflict,)},
        [],
    )

    view = WorkspaceIntelService(gmail, calendar).upcoming_agenda()

    assert len(calendar.conflict_calls) == 2
    assert view.today[0].has_conflict is True
    assert view.today[0].missing_preparation is True
    assert view.tomorrow[0].has_conflict is True
    assert view.tomorrow[0].missing_preparation is True


def test_upcoming_agenda_queries_tomorrow_via_list_today_not_a_week_window() -> None:
    """list_week()'s fixed Mon-Sun window silently drops "tomorrow" whenever
    today is Sunday; upcoming_agenda must instead ask list_today() for
    tomorrow's own local day directly, which has no such boundary case."""
    service, _, calendar = _service()

    service.upcoming_agenda()

    assert len(calendar.list_today_calls) == 2
    first_call, second_call = calendar.list_today_calls
    assert first_call is None
    assert second_call is not None
    assert timedelta(hours=23) <= (second_call - datetime.now(timezone.utc)) <= timedelta(hours=25)


def test_upcoming_agenda_days_one_skips_tomorrow_lookup() -> None:
    service, _, calendar = _service()

    view = service.upcoming_agenda(days=1)

    assert view.tomorrow == ()
    assert calendar.list_today_calls == [None]


def test_upcoming_agenda_handles_empty_calendar() -> None:
    service, _, _ = _service()

    view = service.upcoming_agenda()

    assert view.today == ()
    assert view.tomorrow == ()
    assert view.truncated is False


def test_upcoming_agenda_surfaces_truncation_from_either_day() -> None:
    gmail = FakeGmail(MessageSearchResult((), False), [])
    calendar = FakeCalendar(PaginationResult((), True), PaginationResult(), {}, [])

    view = WorkspaceIntelService(gmail, calendar).upcoming_agenda()

    assert view.truncated is True


def test_workspace_intel_has_no_mutating_workspace_method_references() -> None:
    source = inspect.getsource(workspace_intel_service)
    forbidden = (".send(", ".modify(", ".trash(", ".insert(", ".update(", ".delete(")
    assert all(token not in source for token in forbidden)


def test_workspace_intel_imports_only_dtos_never_constructs_a_google_client() -> None:
    """AST-level check: workspace_intel must import only the read-only DTO
    modules (privacy-scrubbed dataclasses), never auth/factory/service
    modules that could construct a live Google API client."""
    import ast
    import pathlib

    module_path = pathlib.Path(workspace_intel_service.__file__)
    tree = ast.parse(module_path.read_text(), filename=str(module_path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    disallowed = {m for m in imported_modules if m.startswith("app.google_workspace") and not m.endswith(".dtos")}
    assert disallowed == set()
    assert not any("googleapiclient" in m or "google_auth" in m or "oauth" in m.lower() for m in imported_modules)
