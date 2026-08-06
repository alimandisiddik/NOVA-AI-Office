# Night Shift Runtime

## Architecture

`app/nightshift/` is a metadata-only, SQLite-backed service that initializes
alongside the existing NOVA services. It does not alter `run_singleton.py`,
launchd setup, bounded logging, or polling startup, and it does not start a
second daemon or executor loop.

## Runtime Modes

| Mode | Scheduler behavior | Intake behavior |
|---|---|---|
| `active` | Can transition into the scheduled night window | Allowed |
| `night_shift` | Can transition out at scheduled end | Allowed, safe jobs only prepare for approval |
| `quiet` | Sticky manual override | Queued, never automatically processed |
| `maintenance` | Sticky manual override | New overnight jobs rejected |

The scheduler only changes `active` and `night_shift`; manual changes are
recorded in `night_shift_audit_log`. Unknown modes fail closed.

## Schedule

`night_shift_configuration` stores `HH:MM` start, end, morning brief time,
and an IANA timezone (default `Asia/Jakarta`). All calculations convert an
explicit timestamp to that timezone; no host-local timezone is used. A start
after the end is a normal window that crosses midnight.

## Classification and Queue

The registry is a hard allowlist. Safe classifications are `read_only`,
`repeatable`, `reversible`, and `draft_only`; unsafe classes including
`destructive`, `external_communication`, `approval_required`, `paid_action`,
`secret_change`, `document_overwrite`, `calendar_mutation`, `git_mutation`,
and `destructive_migration` are explicitly prohibited. The policy lifecycle
is `prepare → validate → save draft → wait for approval`; no executor exists.

`night_queue_jobs` retains identifiers, timestamps, state, deduplication key,
and scrubbed JSON metadata only. It never accepts raw prompts, documents,
provider responses, secrets, or exception traces.

## Notifications and Briefs

Informational events route to the morning brief, attention events to its
prioritized section, and only data-loss, security, database-corruption, or
repeated essential-service incidents can be critical/immediate-eligible.
No notification is delivered in this sprint.

`morning_briefs` creates one immutable record per local briefing date with
completed, attention, approval, safe-failure, and runtime-health sections.
Regeneration is idempotent. `app/nightshift/brief.py` reserves the future
Telegram delivery boundary for Sprint 5B.

## Manual Smoke Test

1. Start NOVA normally and inspect the SQLite tables after initialization.
2. Set `quiet`, call the scheduler tick across a boundary, and verify mode is unchanged.
3. Queue `draft_summary_prepare`; verify the draft lifecycle ends at approval.
4. Attempt `git_commit` or an unknown type; verify rejection and audit entry.
5. Generate a brief twice for the same `Asia/Jakarta` date; verify the same row is returned.

## Limitations and Future Interfaces

No Telegram commands, external notifications, model calls, file operations,
Git changes, document changes, or background execution are implemented.
Sprint 5B can use `get_runtime_mode`, `set_runtime_mode`, queue methods, and
`get_latest_morning_brief`; a future automation sprint can add a separately
approved executor behind the registered job types.
