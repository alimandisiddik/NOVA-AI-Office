"""Safe, bounded, read-only Google Calendar operations for NOVA."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.google_workspace.auth import AuthError, GoogleDependencyError
from app.google_workspace.factory import GoogleClientFactory

from .audit import CalendarAuditRecord, CalendarAuditSink, utc_now
from .config import CalendarIntegrationConfig
from .dtos import (
    CalendarEvent,
    Conflict,
    EventDraft,
    FreeBusyCalendarError,
    FreeBusyResult,
    MeetingBrief,
    PaginationResult,
    TimeRange,
)
from .exceptions import (
    CalendarAuditError,
    CalendarAuthenticationError,
    CalendarConfigurationError,
    CalendarDependencyError,
    CalendarInvalidRequestError,
    CalendarNetworkError,
    CalendarNotFoundError,
    CalendarPermissionError,
    CalendarProviderError,
    CalendarRateLimitError,
    CalendarServiceError,
)


_SAFE_QUERY = re.compile(r"^[\w\s.,:/&()\-']{1,120}$", re.UNICODE)


class CalendarService:
    """Expose only allowlisted, non-mutating Google Calendar reads."""

    def __init__(
        self,
        client_factory: GoogleClientFactory,
        audit_sink: CalendarAuditSink,
        config: CalendarIntegrationConfig | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._audit_sink = audit_sink
        self._config = config or CalendarIntegrationConfig()
        self._validate_configuration()

    def list_today(
        self,
        timezone_name: str = "Asia/Jakarta",
        calendar_alias: str = "primary",
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> PaginationResult:
        """List the local calendar day [00:00, next 00:00) in an IANA timezone."""
        zone = self._zone(timezone_name)
        local_now = self._local_now(zone, now)
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.search_events(
            window=TimeRange(start=start, end=start + timedelta(days=1)),
            timezone_name=timezone_name,
            calendar_alias=calendar_alias,
            correlation_id=correlation_id,
        )

    def list_week(
        self,
        timezone_name: str = "Asia/Jakarta",
        calendar_alias: str = "primary",
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> PaginationResult:
        """List Monday 00:00 through the following Monday 00:00, local time."""
        zone = self._zone(timezone_name)
        local_now = self._local_now(zone, now)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())
        return self.search_events(
            window=TimeRange(start=week_start, end=week_start + timedelta(days=7)),
            timezone_name=timezone_name,
            calendar_alias=calendar_alias,
            correlation_id=correlation_id,
        )

    def search_events(
        self,
        window: TimeRange | None = None,
        *,
        timezone_name: str = "Asia/Jakarta",
        calendar_alias: str = "primary",
        query: str | None = None,
        max_results: int | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> PaginationResult:
        """Return a globally bounded, deduplicated set of expanded occurrences."""
        operation_start = time.monotonic()
        zone = self._zone(timezone_name)
        try:
            provider_id = self._calendar_id(calendar_alias)
        except CalendarInvalidRequestError:
            self._audit_failure("search_events", operation_start, "unknown", "invalid_request", correlation_id)
            raise
        safe_query = self._sanitize_query(query)
        active_window = window or self._default_window(zone, now)
        limit = self._global_limit(max_results)

        if active_window.end - active_window.start > timedelta(days=self._config.max_search_window_days):
            self._audit_failure("search_events", operation_start, calendar_alias, "invalid_request", correlation_id)
            raise CalendarInvalidRequestError()

        try:
            client = self._client()
            events: dict[str, CalendarEvent] = {}
            page_token: str | None = None
            seen_tokens: set[str] = set()
            truncated = False

            while True:
                if page_token is not None:
                    if page_token in seen_tokens:
                        truncated = True
                        break
                    seen_tokens.add(page_token)

                request: dict[str, Any] = {
                    "calendarId": provider_id,
                    "timeMin": self._rfc3339_utc(active_window.start),
                    "timeMax": self._rfc3339_utc(active_window.end),
                    "singleEvents": True,
                    "showDeleted": False,
                    "orderBy": "startTime",
                    "maxResults": min(250, max(1, limit - len(events))),
                }
                if page_token is not None:
                    request["pageToken"] = page_token
                if safe_query is not None:
                    request["q"] = safe_query

                response = client.events().list(**request).execute()
                for raw_event in response.get("items", ()):
                    event = self._event_from_provider(raw_event, zone)
                    if event is None or event.id in events:
                        continue
                    events[event.id] = event
                    if len(events) >= limit:
                        break

                next_token = response.get("nextPageToken")
                if len(events) >= limit:
                    truncated = bool(next_token)
                    break
                if not next_token:
                    break
                if next_token in seen_tokens:
                    truncated = True
                    break
                page_token = next_token

            result = tuple(
                sorted(events.values(), key=lambda event: (event.start.astimezone(timezone.utc), event.id))
            )
            self._audit_success("search_events", operation_start, calendar_alias, len(result), correlation_id)
            return PaginationResult(events=result, truncated=truncated)
        except CalendarServiceError as error:
            self._audit_failure("search_events", operation_start, calendar_alias, error.category, correlation_id)
            raise
        except Exception as error:
            mapped = self._map_provider_error(error)
            self._audit_failure("search_events", operation_start, calendar_alias, mapped.category, correlation_id)
            raise mapped from None

    def get_free_busy(
        self,
        window: TimeRange,
        calendar_aliases: Sequence[str],
        correlation_id: str | None = None,
    ) -> FreeBusyResult:
        """Get privacy-safe busy intervals for an approved, bounded alias set."""
        operation_start = time.monotonic()
        aliases = tuple(calendar_aliases)
        if (
            not aliases
            or len(aliases) > self._config.max_calendar_identifiers
            or len(set(aliases)) != len(aliases)
            or window.end - window.start > timedelta(days=self._config.max_search_window_days)
        ):
            self._audit_failure("get_free_busy", operation_start, "approved-set", "invalid_request", correlation_id)
            raise CalendarInvalidRequestError()

        resolved = tuple((alias, self._calendar_id(alias)) for alias in aliases)
        identifier_to_alias = {identifier: alias for alias, identifier in resolved}
        try:
            client = self._client()
            response = client.freebusy().query(
                body={
                    "timeMin": self._rfc3339_utc(window.start),
                    "timeMax": self._rfc3339_utc(window.end),
                    "items": tuple({"id": identifier} for _, identifier in resolved),
                }
            ).execute()

            busy: set[tuple[datetime, datetime]] = set()
            calendar_errors: list[FreeBusyCalendarError] = []
            for identifier, payload in response.get("calendars", {}).items():
                alias = identifier_to_alias.get(identifier, "approved-calendar")
                if payload.get("errors"):
                    calendar_errors.append(FreeBusyCalendarError(alias, "provider_failure"))
                    continue
                for slot in payload.get("busy", ()):
                    parsed = self._provider_range(slot)
                    if parsed is not None:
                        busy.add((parsed.start, parsed.end))

            result = tuple(TimeRange(start=start, end=end) for start, end in sorted(busy))
            self._audit_success("get_free_busy", operation_start, "approved-set", len(result), correlation_id)
            return FreeBusyResult(window, result, tuple(calendar_errors))
        except CalendarServiceError as error:
            self._audit_failure("get_free_busy", operation_start, "approved-set", error.category, correlation_id)
            raise
        except Exception as error:
            mapped = self._map_provider_error(error)
            self._audit_failure("get_free_busy", operation_start, "approved-set", mapped.category, correlation_id)
            raise mapped from None

    def detect_conflicts(
        self,
        window: TimeRange,
        calendar_alias: str = "primary",
        correlation_id: str | None = None,
    ) -> tuple[Conflict, ...]:
        """Return unique non-zero overlaps; merely adjacent events do not conflict."""
        result = self.search_events(
            window,
            calendar_alias=calendar_alias,
            correlation_id=correlation_id,
        )
        conflicts: list[Conflict] = []
        seen: set[str] = set()
        for event in result.events:
            overlap_start = max(event.start, window.start)
            overlap_end = min(event.end, window.end)
            if overlap_start >= overlap_end or event.id in seen:
                continue
            seen.add(event.id)
            conflicts.append(
                Conflict(event.id, event.title, TimeRange(start=overlap_start, end=overlap_end))
            )
        return tuple(conflicts)

    def build_meeting_brief(
        self, event: CalendarEvent, project_reference: str | None = None
    ) -> MeetingBrief:
        """Return an already-sanitized event summary without expanding privacy scope."""
        return MeetingBrief(
            title=event.title,
            time_range=event.time_range,
            timezone=event.timezone,
            location=event.location,
            attendee_count=event.attendee_count,
            organizer_alias=event.organizer_alias,
            conference_provider=event.conference_provider,
            project_reference=project_reference,
            event_id=event.id,
        )

    def prepare_event_draft(
        self,
        title: str,
        window: TimeRange,
        *,
        timezone_name: str = "Asia/Jakarta",
        all_day: bool = False,
        location: str | None = None,
    ) -> EventDraft:
        """Create a local DTO only; this method never acquires a Google client."""
        zone = self._zone(timezone_name)
        clean_title = self._bounded_text(title, 160)
        if not clean_title:
            raise CalendarInvalidRequestError()
        clean_location = self._bounded_text(location, 200) if location is not None else None
        if location is not None and clean_location is None:
            raise CalendarInvalidRequestError()

        normalized = TimeRange(window.start.astimezone(zone), window.end.astimezone(zone))
        if all_day and any(
            value != 0
            for point in (normalized.start, normalized.end)
            for value in (point.hour, point.minute, point.second, point.microsecond)
        ):
            raise CalendarInvalidRequestError()
        return EventDraft(clean_title, normalized, timezone_name, all_day, clean_location)

    def _client(self) -> Any:
        try:
            return self._client_factory.get_service("calendar", "v3")
        except GoogleDependencyError as error:
            raise CalendarDependencyError() from None
        except AuthError as error:
            raise CalendarAuthenticationError() from None

    def _calendar_id(self, alias: str) -> str:
        calendar_id = self._config.aliases().get(alias)
        if not calendar_id:
            raise CalendarInvalidRequestError()
        return calendar_id

    def _validate_configuration(self) -> None:
        aliases = self._config.aliases()
        if (
            not aliases
            or any(not isinstance(alias, str) or not alias or not isinstance(identifier, str) or not identifier for alias, identifier in aliases.items())
            or self._config.default_search_window_days < 1
            or self._config.max_search_window_days < self._config.default_search_window_days
            or self._config.default_global_result_limit < 1
            or self._config.max_global_result_limit < self._config.default_global_result_limit
            or self._config.max_calendar_identifiers < 1
        ):
            raise CalendarConfigurationError()
        try:
            ZoneInfo(self._config.default_timezone)
        except ZoneInfoNotFoundError:
            raise CalendarConfigurationError() from None

    def _zone(self, timezone_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(timezone_name)
        except (TypeError, ZoneInfoNotFoundError):
            raise CalendarInvalidRequestError() from None

    @staticmethod
    def _local_now(zone: ZoneInfo, now: datetime | None) -> datetime:
        if now is not None and (now.tzinfo is None or now.utcoffset() is None):
            raise CalendarInvalidRequestError()
        return (now or datetime.now(timezone.utc)).astimezone(zone)

    def _default_window(self, zone: ZoneInfo, now: datetime | None) -> TimeRange:
        local_now = self._local_now(zone, now)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return TimeRange(day_start, day_start + timedelta(days=self._config.default_search_window_days))

    def _global_limit(self, requested: int | None) -> int:
        limit = self._config.default_global_result_limit if requested is None else requested
        if not isinstance(limit, int) or limit < 1 or limit > self._config.max_global_result_limit:
            raise CalendarInvalidRequestError()
        return limit

    @staticmethod
    def _rfc3339_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _bounded_text(value: str | None, maximum: int) -> str | None:
        if value is None or not isinstance(value, str) or len(value) > maximum or any(
            ord(char) < 32 for char in value
        ):
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    def _sanitize_query(self, query: str | None) -> str | None:
        if query is None:
            return None
        cleaned = self._bounded_text(query, 120)
        if cleaned is None or not _SAFE_QUERY.fullmatch(cleaned):
            raise CalendarInvalidRequestError()
        return cleaned

    def _event_from_provider(self, raw: Any, requested_zone: ZoneInfo) -> CalendarEvent | None:
        if not isinstance(raw, dict) or raw.get("status") == "cancelled":
            return None
        # singleEvents=True asks Google to expand recurrence. Excluding masters prevents double counting.
        if raw.get("recurrence") and not raw.get("recurringEventId"):
            return None

        event_range, all_day, tz_name = self._provider_event_range(raw, requested_zone)
        if event_range is None:
            return None
        event_id = raw.get("id")
        if not isinstance(event_id, str) or not event_id:
            return None

        private = raw.get("visibility") in {"private", "confidential"}
        title = "Private event" if private else self._bounded_text(raw.get("summary", "Untitled Event"), 160)
        if title is None:
            title = "Untitled Event"
        attendee_count = len(raw.get("attendees", ())) if isinstance(raw.get("attendees", ()), list) else 0
        organizer_alias = None
        organizer = raw.get("organizer", {})
        if not private and isinstance(organizer, dict) and isinstance(organizer.get("email"), str):
            organizer_alias = "org_" + hashlib.sha256(organizer["email"].encode()).hexdigest()[:12]

        location = None if private else self._bounded_text(raw.get("location"), 200)
        conference_provider = None if private else self._conference_provider(raw)
        recurrence_id = raw.get("recurringEventId")
        if not isinstance(recurrence_id, str):
            recurrence_id = None
        return CalendarEvent(
            id=event_id,
            title=title,
            time_range=event_range,
            timezone=tz_name,
            all_day=all_day,
            location=location,
            attendee_count=attendee_count,
            organizer_alias=organizer_alias,
            conference_provider=conference_provider,
            recurring_event_id=recurrence_id,
        )

    def _provider_event_range(
        self, raw: dict[str, Any], requested_zone: ZoneInfo
    ) -> tuple[TimeRange | None, bool, str]:
        start = raw.get("start")
        end = raw.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            return None, False, str(requested_zone)
        tz_name = start.get("timeZone") if isinstance(start.get("timeZone"), str) else str(requested_zone)
        try:
            event_zone = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            event_zone = requested_zone
            tz_name = str(requested_zone)
        try:
            if isinstance(start.get("dateTime"), str) and isinstance(end.get("dateTime"), str):
                start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
                return TimeRange(start_dt.astimezone(event_zone), end_dt.astimezone(event_zone)), False, tz_name
            if isinstance(start.get("date"), str) and isinstance(end.get("date"), str):
                start_dt = datetime.fromisoformat(start["date"]).replace(tzinfo=event_zone)
                end_dt = datetime.fromisoformat(end["date"]).replace(tzinfo=event_zone)
                return TimeRange(start_dt, end_dt), True, tz_name
        except (TypeError, ValueError):
            return None, False, tz_name
        return None, False, tz_name

    @staticmethod
    def _conference_provider(raw: dict[str, Any]) -> str | None:
        entry_points = raw.get("conferenceData", {}).get("entryPoints", ())
        if any(isinstance(point, dict) and point.get("entryPointType") == "video" for point in entry_points):
            return "video"
        return None

    @staticmethod
    def _provider_range(slot: Any) -> TimeRange | None:
        if not isinstance(slot, dict):
            return None
        try:
            start = datetime.fromisoformat(slot["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(slot["end"].replace("Z", "+00:00"))
            return TimeRange(start, end)
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _map_provider_error(error: Exception) -> CalendarServiceError:
        status = getattr(error, "status_code", None)
        if not isinstance(status, int):
            response = getattr(error, "resp", None)
            status = getattr(response, "status", None)
        if status == 401:
            return CalendarAuthenticationError()
        if status == 403:
            return CalendarPermissionError()
        if status == 404:
            return CalendarNotFoundError()
        if status == 429:
            return CalendarRateLimitError()
        if status in {400, 409, 422}:
            return CalendarInvalidRequestError()
        if isinstance(error, (TimeoutError, ConnectionError, OSError)):
            return CalendarNetworkError()
        return CalendarProviderError()

    def _audit_success(
        self, operation: str, start: float, alias: str, count: int, correlation_id: str | None
    ) -> None:
        self._record_audit(operation, start, alias, count, True, "success", correlation_id)

    def _audit_failure(
        self, operation: str, start: float, alias: str, category: str, correlation_id: str | None
    ) -> None:
        self._record_audit(operation, start, alias, 0, False, category, correlation_id)

    def _record_audit(
        self,
        operation: str,
        start: float,
        alias: str,
        count: int,
        success: bool,
        category: str,
        correlation_id: str | None,
    ) -> None:
        record = CalendarAuditRecord(
            timestamp=utc_now(),
            operation=operation,
            calendar_hash=hashlib.sha256(alias.encode()).hexdigest(),
            result_count=count,
            duration_ms=max(0, int((time.monotonic() - start) * 1000)),
            success=success,
            category=category,
            correlation_id=correlation_id,
        )
        try:
            self._audit_sink.record(record)
        except Exception:
            raise CalendarAuditError() from None
