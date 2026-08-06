# Google Calendar Integration

## Service Interface
`CalendarService` is initialized with the Sprint 5C `GoogleClientFactory`, a
`CalendarAuditSink`, and optional `CalendarIntegrationConfig`. It exposes:

- `list_today(...)` and `list_week(...)` for agenda reads.
- `search_events(...)` for an allowlisted calendar alias and bounded interval.
- `get_free_busy(...)` for allowlisted calendar aliases only.
- `detect_conflicts(...)` for unique non-zero overlaps with a requested range.
- `build_meeting_brief(...)` and `prepare_event_draft(...)` for safe local DTOs.

No method calls a Calendar mutation endpoint. `prepare_event_draft` only validates
and returns an `EventDraft`; it never obtains a Google client or claims an event
was created.

## Scopes and Configuration
The module exports `CALENDAR_SCOPES`, which is exactly Sprint 5C's approved
`ScopeBundle.CALENDAR`. The bundle includes Calendar read-only access plus the
identity scopes used by the Workspace foundation. The module requests no raw,
Drive, Gmail, or mutation scopes.

`CalendarIntegrationConfig` owns only Calendar policy: approved alias-to-provider
calendar mappings, the default timezone, result limits, and bounded time windows.
The default timezone is `Asia/Jakarta`; there is no edit to `app/config.py`.

## Timezone Rules
“Today” means `[local midnight, next local midnight)` in the requested IANA
timezone, never host-local or UTC-derived. The default is `Asia/Jakarta`.
“Week” means Monday 00:00 local through the following Monday 00:00 local.
Both are converted to RFC3339 UTC provider query boundaries. `zoneinfo` provides
DST-capable behavior. All DTO time ranges are timezone-aware; all-day events use
their exclusive end-date boundary, and cross-midnight events preserve both times.
Invalid timezone names and naive/empty ranges fail as `CalendarInvalidRequestError`.

## Search, Pagination, and Recurrence
Search requires a valid `TimeRange` or applies a documented safe default: the
next seven local calendar days. Windows cannot exceed 31 days. The optional text
query has a bounded, allowlisted character set; callers cannot pass arbitrary
Google request arguments.

Provider reads always use `singleEvents=True`, `showDeleted=False`, and
`orderBy="startTime"`. This delegates recurrence expansion to Google, excludes
cancelled occurrences, ignores any unexpected recurrence masters, follows tokens
until exhaustion/global limit, stops repeated-token loops, deduplicates stable
event IDs, sorts deterministically, and exposes only a `truncated` flag—not raw
pagination tokens.

## Privacy Model
Provider-to-DTO conversion excludes descriptions, attachments, internal notes,
attendee names/emails, and raw conferencing URLs. Attendee count is retained.
Organizer emails are replaced with a deterministic short hash alias for ordinary
events. Private or confidential events become `Private event` and suppress
location, organizer alias, and conferencing metadata. Conferencing is limited to
a generic safe provider category (`video`) where applicable.

## Free/Busy and Conflicts
Free/busy accepts only configured aliases, rejects empty, duplicate, malformed,
or excessive alias lists, and bounds the queried range. Per-calendar failures are
returned as typed safe categories without titles, attendees, raw IDs, or provider
bodies. Conflict detection uses strict interval overlap: adjacent endpoints do
not conflict, while all-day and cross-midnight intersections do.

## Errors and Audit
Errors have stable categories: dependency, configuration, authentication,
permission, rate-limit, not-found, network/timeout, invalid-request, and provider
failure. Retryability is explicit on rate-limit/network/provider failures. Raw
provider texts and bodies are never included in exceptions.

Every attempted provider operation writes a `CalendarAuditRecord` to the supplied
sink with only operation, SHA-256 hash of an approved alias, result count,
duration, outcome category, UTC timestamp, and optional Control Tower correlation
ID. Event fields, tokens, credential paths, raw exceptions, and provider bodies
are never recorded. Audit persistence failures raise a sanitized error rather
than silently bypassing the audit boundary.

## Operational Limitations
This sprint intentionally does not provide Calendar write execution, invitation
handling, a persistent Control Tower audit repository, or live credential/network
validation. The in-memory audit sink is a safe integration boundary for later
approved wiring.
