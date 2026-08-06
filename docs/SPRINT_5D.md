# Sprint 5D — Google Calendar Integration

## Objective
Deliver a safe, read-first Google Calendar integration that returns bounded,
privacy-safe DTOs to future Executive Control Tower wiring. It uses Sprint 5C's
`GoogleAuthenticator`, approved scope registry, credential checks, Google client
factory, and local audit conventions; it does not own OAuth or token storage.

## Scope
- Read-only agenda (`list_today`, `list_week`, `search_events`), free/busy, and
  conflict detection.
- Meeting-brief DTO generation and local-only event-draft preparation.
- Immutable DTOs, typed sanitized errors, and safe in-memory audit records.

## Exclusions
- No Calendar mutation: no create, update, delete, cancellation, or invitations.
- No Telegram commands, Control Tower work-item/storage changes, approvals,
  Drive, Gmail, dissertation behavior, or agent execution.
- No real Google credentials or network calls in this sprint's tests.

## Deliverables
- `app/google_workspace/calendar/`: service, DTOs, errors, config, and audit sink.
- `tests/google_workspace/calendar/`: mock-only Calendar coverage in the normal
  project test path.
- `docs/google-calendar-integration.md`: operational interface and safety model.

## Acceptance Criteria
- Uses only Sprint 5C's `ScopeBundle.CALENDAR`, including Calendar read-only
  access and no Drive/Gmail scope.
- Defaults to `Asia/Jakarta`; date and week queries compute local boundaries and
  query the provider using RFC3339 UTC timestamps.
- Pages through provider results within a global result limit, deduplicates IDs,
  expands recurrences with `singleEvents=True`, and excludes cancelled events.
- Safely handles all-day, cross-midnight, and DST-capable-zone events.
- Validates free/busy aliases and bounded ranges; returns no event titles,
  attendees, or provider body data from free/busy.
- Protects private/confidential events during provider-to-DTO conversion.
- Persists only permitted audit metadata and never silently uses a no-op audit path.
- Tests remain mock-only and cover the required behavior without mutation calls.

## Test Coverage
Focused tests cover Jakarta and DST boundaries, Monday-to-Monday week semantics,
pagination limits/loops/deduplication, recurrence and cancellation treatment,
search bounds, free/busy allowlisting, conflict edge cases, privacy suppression,
draft validation, error taxonomy, audit privacy, and no-mutation behavior.
