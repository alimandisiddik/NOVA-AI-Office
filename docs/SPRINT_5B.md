# Sprint 5B — Executive Control Tower MVP

**Status: implementation under cooperative review; not merged.**

Sprint 5B adds a metadata-only orchestration layer. It does not invoke providers,
Calendar, Drive, external messaging, Night Shift execution, or Git operations.

## Design

- `ControlTowerService` is created once in `app/main.py`, initialized once, and
  receives the existing `ExecutionService` and `NightShiftService` as read-only
  collaborators.
- `build_application()` receives that same service explicitly and stores it in
  `Application.bot_data["control_tower"]`; handlers retrieve it through a
  private accessor. There is no mutable service global.
- `/decision` has one registration. Pipe-delimited input preserves the legacy
  `Project | decision | rationale` path; non-pipe input records a Control Tower
  decision candidate. This avoids handler shadowing.

## Validation and storage

- Capture accepts only the documented categories, validates an optional existing
  project ID, trims/bounds content, rejects secret-shaped or control-character
  content, and normalizes ISO-8601 deadlines to UTC. Naive local values are
  intentionally interpreted in `Asia/Jakarta`.
- Routes are persisted inline on the work item because a capture has exactly one
  non-executing recommendation in this MVP. Every approved category maps to a
  named agent; no recommendation executes anything.
- Dependencies must exist, are unique, cannot self-reference, and are enforced
  with database foreign keys. Because dependencies are immutable at creation and
  must point to existing work items, new circular chains cannot be created.
- `completed` and `cancelled` dependencies are resolved; all other states block.
  Priority and Night Shift eligibility use unresolved blockers, not raw counts.

## Integration

- Approval inbox aggregates pending Control Tower links and work items, existing
  execution approvals, and Night Shift approval-required queue items. It only
  reports requests and never approves or dispatches them.
- Morning briefs retrieve the actual `NightShiftService.get_latest_morning_brief()`
  when injected. Shutdown inspects `list_night_jobs()` only; it never schedules or
  executes a Night Shift job.
- Audit rows record operation, entity ID/type, state changes, actor, correlation
  ID, safe metadata, outcome, and timezone-aware timestamp. They exclude raw
  Telegram payloads, SQL, stack traces, providers, and secrets.
