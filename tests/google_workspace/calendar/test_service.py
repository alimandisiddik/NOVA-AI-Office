"""Mock-only direct coverage for Sprint 5D CalendarService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.google_workspace.auth import AuthError, GoogleDependencyError
from app.google_workspace.calendar.audit import InMemoryCalendarAuditSink
from app.google_workspace.calendar.config import CALENDAR_SCOPES, CalendarIntegrationConfig
from app.google_workspace.calendar.dtos import TimeRange
from app.google_workspace.calendar.exceptions import (
    CalendarAuthenticationError,
    CalendarDependencyError,
    CalendarInvalidRequestError,
    CalendarNetworkError,
    CalendarNotFoundError,
    CalendarPermissionError,
    CalendarProviderError,
    CalendarRateLimitError,
)
from app.google_workspace.calendar.service import CalendarService
from app.google_workspace.factory import GoogleClientFactory
from app.google_workspace.scopes import GoogleScope


UTC = timezone.utc


def when(start: str, end: str) -> TimeRange:
    return TimeRange(
        datetime.fromisoformat(start.replace("Z", "+00:00")),
        datetime.fromisoformat(end.replace("Z", "+00:00")),
    )


def event(event_id: str, start: str, end: str, **extra: object) -> dict[str, object]:
    return {
        "id": event_id,
        "summary": "Planning",
        "start": {"dateTime": start, "timeZone": "UTC"},
        "end": {"dateTime": end, "timeZone": "UTC"},
        **extra,
    }


@pytest.fixture
def factory() -> MagicMock:
    return MagicMock(spec=GoogleClientFactory)


@pytest.fixture
def audit() -> InMemoryCalendarAuditSink:
    return InMemoryCalendarAuditSink()


@pytest.fixture
def service(factory: MagicMock, audit: InMemoryCalendarAuditSink) -> CalendarService:
    return CalendarService(factory, audit)


@pytest.fixture
def client(factory: MagicMock) -> MagicMock:
    instance = MagicMock()
    factory.get_service.return_value = instance
    return instance


def test_calendar_scope_bundle_is_read_only() -> None:
    assert GoogleScope.CALENDAR_READONLY.value in CALENDAR_SCOPES
    assert all("drive" not in scope and "gmail" not in scope for scope in CALENDAR_SCOPES)


def test_today_uses_jakarta_local_boundaries_as_utc(service: CalendarService, client: MagicMock) -> None:
    client.events.return_value.list.return_value.execute.return_value = {"items": []}
    now = datetime(2026, 8, 6, 16, 10, tzinfo=UTC)

    service.list_today(now=now)

    call = client.events.return_value.list.call_args.kwargs
    assert call["timeMin"] == "2026-08-05T17:00:00Z"
    assert call["timeMax"] == "2026-08-06T17:00:00Z"


def test_today_supports_dst_capable_timezone(service: CalendarService, client: MagicMock) -> None:
    client.events.return_value.list.return_value.execute.return_value = {"items": []}

    service.list_today("America/New_York", now=datetime(2026, 3, 8, 12, 0, tzinfo=UTC))

    call = client.events.return_value.list.call_args.kwargs
    assert call["timeMin"] == "2026-03-08T05:00:00Z"
    assert call["timeMax"] == "2026-03-09T04:00:00Z"


def test_week_starts_monday_and_ends_following_monday(service: CalendarService, client: MagicMock) -> None:
    client.events.return_value.list.return_value.execute.return_value = {"items": []}

    service.list_week("Asia/Jakarta", now=datetime(2026, 8, 6, 12, 0, tzinfo=UTC))

    call = client.events.return_value.list.call_args.kwargs
    assert call["timeMin"] == "2026-08-02T17:00:00Z"
    assert call["timeMax"] == "2026-08-09T17:00:00Z"


def test_rejects_invalid_timezone(service: CalendarService) -> None:
    with pytest.raises(CalendarInvalidRequestError):
        service.list_today("not/a-timezone")


def test_search_default_window_is_bounded(service: CalendarService, client: MagicMock) -> None:
    client.events.return_value.list.return_value.execute.return_value = {"items": []}
    service.search_events(now=datetime(2026, 8, 6, 6, tzinfo=UTC))
    call = client.events.return_value.list.call_args.kwargs
    assert call["timeMin"] == "2026-08-05T17:00:00Z"
    assert call["timeMax"] == "2026-08-12T17:00:00Z"


def test_search_rejects_excessive_window_and_invalid_text(service: CalendarService) -> None:
    with pytest.raises(CalendarInvalidRequestError):
        service.search_events(when("2026-01-01T00:00:00Z", "2026-03-01T00:00:00Z"))
    with pytest.raises(CalendarInvalidRequestError):
        service.search_events(when("2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"), query="bad\nquery")


def test_pagination_deduplicates_recurring_expansion_and_hides_cancelled(
    service: CalendarService, client: MagicMock
) -> None:
    responses = iter(
        [
            {
                "items": [
                    event("instance-1", "2026-08-06T09:00:00Z", "2026-08-06T10:00:00Z", recurringEventId="master"),
                    event("master", "2026-08-01T09:00:00Z", "2026-08-01T10:00:00Z", recurrence=["RRULE:FREQ=DAILY"]),
                ],
                "nextPageToken": "page-2",
            },
            {
                "items": [
                    event("instance-1", "2026-08-06T09:00:00Z", "2026-08-06T10:00:00Z", recurringEventId="master"),
                    event("instance-2", "2026-08-07T09:00:00Z", "2026-08-07T10:00:00Z", recurringEventId="master"),
                    {"id": "cancelled", "status": "cancelled"},
                ]
            },
        ]
    )
    client.events.return_value.list.return_value.execute.side_effect = lambda: next(responses)

    result = service.search_events(when("2026-08-01T00:00:00Z", "2026-08-10T00:00:00Z"))

    assert [item.id for item in result.events] == ["instance-1", "instance-2"]
    first_call = client.events.return_value.list.call_args_list[0].kwargs
    assert first_call["singleEvents"] is True
    assert first_call["showDeleted"] is False


def test_pagination_respects_global_limit_and_repeated_tokens(service: CalendarService, client: MagicMock) -> None:
    client.events.return_value.list.return_value.execute.side_effect = [
        {"items": [event("a", "2026-08-01T01:00:00Z", "2026-08-01T02:00:00Z")], "nextPageToken": "again"},
        {"items": [event("b", "2026-08-01T03:00:00Z", "2026-08-01T04:00:00Z")], "nextPageToken": "again"},
    ]
    result = service.search_events(when("2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"), max_results=5)
    assert [item.id for item in result.events] == ["a", "b"]
    assert result.truncated is True
    assert client.events.return_value.list.call_count == 2


def test_global_limit_across_pages(service: CalendarService, client: MagicMock) -> None:
    client.events.return_value.list.return_value.execute.side_effect = [
        {"items": [event("a", "2026-08-01T01:00:00Z", "2026-08-01T02:00:00Z")], "nextPageToken": "two"},
        {"items": [event("b", "2026-08-01T03:00:00Z", "2026-08-01T04:00:00Z")], "nextPageToken": "three"},
    ]
    result = service.search_events(when("2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z"), max_results=2)
    assert [item.id for item in result.events] == ["a", "b"]
    assert result.truncated is True


def test_all_day_and_cross_midnight_events_preserved(service: CalendarService, client: MagicMock) -> None:
    client.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": "day", "summary": "Holiday", "start": {"date": "2026-08-06"}, "end": {"date": "2026-08-07"}},
            event("night", "2026-08-06T22:00:00Z", "2026-08-07T02:00:00Z"),
        ]
    }
    result = service.search_events(when("2026-08-06T00:00:00Z", "2026-08-08T00:00:00Z"))
    assert result.events[0].all_day is True
    assert result.events[1].end.date() == datetime(2026, 8, 7, tzinfo=UTC).date()


def test_free_busy_accepts_only_configured_aliases_and_sanitizes_errors(
    service: CalendarService, client: MagicMock
) -> None:
    client.freebusy.return_value.query.return_value.execute.return_value = {
        "calendars": {"primary": {"busy": [{"start": "2026-08-06T10:00:00Z", "end": "2026-08-06T11:00:00Z"}]}}
    }
    result = service.get_free_busy(when("2026-08-06T00:00:00Z", "2026-08-07T00:00:00Z"), ["primary"])
    assert len(result.busy_slots) == 1
    with pytest.raises(CalendarInvalidRequestError):
        service.get_free_busy(when("2026-08-06T00:00:00Z", "2026-08-07T00:00:00Z"), ["other"])
    with pytest.raises(CalendarInvalidRequestError):
        service.get_free_busy(when("2026-08-06T00:00:00Z", "2026-08-07T00:00:00Z"), [])


def test_conflicts_exclude_adjacent_and_include_all_day_and_cross_midnight(
    service: CalendarService, client: MagicMock
) -> None:
    client.events.return_value.list.return_value.execute.return_value = {
        "items": [
            event("adjacent", "2026-08-06T08:00:00Z", "2026-08-06T10:00:00Z"),
            event("overnight", "2026-08-06T23:00:00Z", "2026-08-07T03:00:00Z"),
            {"id": "all-day", "summary": "Holiday", "start": {"date": "2026-08-06"}, "end": {"date": "2026-08-07"}},
        ]
    }
    conflicts = service.detect_conflicts(when("2026-08-06T10:00:00Z", "2026-08-07T01:00:00Z"))
    assert [conflict.event_id for conflict in conflicts] == ["all-day", "overnight"]


def test_time_range_rejects_naive_zero_and_reversed_values() -> None:
    with pytest.raises(ValueError):
        TimeRange(datetime(2026, 8, 1), datetime(2026, 8, 2))
    with pytest.raises(ValueError):
        TimeRange(datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC))


def test_private_provider_fields_are_suppressed_and_organizer_is_minimized(
    service: CalendarService, client: MagicMock
) -> None:
    client.events.return_value.list.return_value.execute.return_value = {
        "items": [
            event(
                "private",
                "2026-08-06T10:00:00Z",
                "2026-08-06T11:00:00Z",
                summary="Budget redraft",
                visibility="private",
                location="Secret room",
                description="must not escape",
                attendees=[{"email": "person@example.com"}],
                organizer={"email": "owner@example.com"},
                conferenceData={"entryPoints": [{"entryPointType": "video", "uri": "https://secret.example"}]},
            ),
            event("normal", "2026-08-06T12:00:00Z", "2026-08-06T13:00:00Z", organizer={"email": "owner@example.com"}),
        ]
    }
    result = service.search_events(when("2026-08-06T00:00:00Z", "2026-08-07T00:00:00Z"))
    private, normal = result.events
    assert private.title == "Private event"
    assert private.location is None and private.organizer_alias is None and private.conference_provider is None
    assert not hasattr(private, "description") and not hasattr(private, "attendees")
    assert normal.organizer_alias == "org_" + __import__("hashlib").sha256(b"owner@example.com").hexdigest()[:12]


def test_meeting_brief_is_privacy_safe(service: CalendarService, client: MagicMock) -> None:
    client.events.return_value.list.return_value.execute.return_value = {"items": [event("event", "2026-08-06T10:00:00Z", "2026-08-06T11:00:00Z")]}
    calendar_event = service.search_events(when("2026-08-06T00:00:00Z", "2026-08-07T00:00:00Z")).events[0]
    brief = service.build_meeting_brief(calendar_event, "PRJ-1")
    assert brief.project_reference == "PRJ-1"
    assert not hasattr(brief, "attendees")


def test_draft_is_local_only_and_validates_fields(service: CalendarService, factory: MagicMock) -> None:
    range_ = when("2026-08-06T10:00:00Z", "2026-08-06T11:00:00Z")
    draft = service.prepare_event_draft(" Planning ", range_, timezone_name="Asia/Jakarta", location="Room A")
    assert draft.title == "Planning" and draft.timezone == "Asia/Jakarta"
    factory.get_service.assert_not_called()
    with pytest.raises(CalendarInvalidRequestError):
        service.prepare_event_draft("", range_)
    with pytest.raises(CalendarInvalidRequestError):
        service.prepare_event_draft("Draft", range_, all_day=True)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthError("required"), CalendarAuthenticationError),
        (GoogleDependencyError("missing"), CalendarDependencyError),
        (TimeoutError(), CalendarNetworkError),
        (type("Http", (Exception,), {"status_code": 403})(), CalendarPermissionError),
        (type("Http", (Exception,), {"status_code": 429})(), CalendarRateLimitError),
        (type("Http", (Exception,), {"status_code": 404})(), CalendarNotFoundError),
        (Exception("unknown provider body must never be exposed"), CalendarProviderError),
    ],
)
def test_error_taxonomy_is_sanitized(
    service: CalendarService, factory: MagicMock, client: MagicMock, error: Exception, expected: type[Exception]
) -> None:
    if isinstance(error, (AuthError, GoogleDependencyError)):
        factory.get_service.side_effect = error
    else:
        client.events.return_value.list.return_value.execute.side_effect = error
    with pytest.raises(expected) as raised:
        service.search_events(when("2026-08-06T00:00:00Z", "2026-08-07T00:00:00Z"))
    assert "provider body" not in str(raised.value)


def test_audit_persists_safe_metadata_only(service: CalendarService, client: MagicMock, audit: InMemoryCalendarAuditSink) -> None:
    client.events.return_value.list.return_value.execute.return_value = {"items": [event("event", "2026-08-06T10:00:00Z", "2026-08-06T11:00:00Z", summary="Sensitive title")]}
    service.search_events(when("2026-08-06T00:00:00Z", "2026-08-07T00:00:00Z"), correlation_id="tower-123")
    record = audit.records()[0]
    assert record.success and record.correlation_id == "tower-123"
    assert record.timestamp.tzinfo is not None
    assert "primary" not in record.calendar_hash and "Sensitive title" not in repr(record)


def test_no_mutation_endpoint_is_called(service: CalendarService, client: MagicMock) -> None:
    client.events.return_value.list.return_value.execute.return_value = {"items": []}
    service.search_events(when("2026-08-06T00:00:00Z", "2026-08-07T00:00:00Z"))
    client.events.return_value.insert.assert_not_called()
    client.events.return_value.update.assert_not_called()
    client.events.return_value.delete.assert_not_called()
