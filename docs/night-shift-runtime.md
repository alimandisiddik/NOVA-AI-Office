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

---

## Sprint 5F: Automation (`NightShiftWorker`)

The executor referenced above is `app/nightshift/worker.py`'s
`NightShiftWorker`. It consumes Sprint 5B.1's `app/dispatch/`
(`DispatchService`, `ApprovalService`, `AgentRegistry`) and owns no
dispatch/approval state of its own — see `docs/agent-dispatch-and-approvals.md`
for that layer, and `docs/WAVE_3_INTEGRATION_CONTRACT.md` for the full
cross-sprint interface contract.

### Scheduler

`application.job_queue.run_repeating(_nightshift_tick, interval=300,
first=10, data={"night_shift": ..., "night_worker": ...})` in
`build_application()` (`app/telegram_bot.py`) registers the automation tick
exactly once. The callback retrieves its dependencies via
`context.job.data` — the correct accessor on the installed
python-telegram-bot 22.8 (the pre-20 `context=`/`Job.context` API was
removed upstream). A non-blocking lock skips an overlapping tick rather than
letting two run concurrently. Each eligible job's claim/execute pass and
each stale-recovery item are wrapped independently, so one job's exception
never stops the rest of the tick.

### Claim and lease

`night_queue_jobs` gained five additive columns (`dispatch_id`,
`lease_worker_id`, `lease_expires_at`, `attempt_count`, `approval_id`),
applied via `PRAGMA table_info(night_queue_jobs)` existence checks before
each `ALTER TABLE ... ADD COLUMN` — never a blind
`try/except sqlite3.OperationalError`. `claim_job()`'s lease acquisition is
a single atomic `UPDATE ... WHERE status IN ('queued','preparing') AND
(lease_worker_id IS NULL OR lease_expires_at <= ?)`; a CAS miss raises
`NightJobAlreadyClaimedError`. This correctly rejects reclaiming a job in
any terminal or waiting state (`completed`, `rejected`, `failed_safely`,
`awaiting_approval`, `draft_saved`) and rejects a currently-active lease
held by another worker; an expired lease is safely reclaimable.

### State transitions

Every status change the worker makes goes through
`NightShiftService.transition_night_job()`; the worker never writes
`night_queue_jobs.status` directly, and `JOB_TRANSITIONS` itself is
**unmodified** from 5A.1. Because the original transition graph only
reaches `completed` via `awaiting_approval` (a real human decision), an
approval-free automated job whose dispatch succeeds is advanced through
`preparing → validating → draft_saved` and rests there — the deepest legal
state reachable without faking approval — with the real outcome (a linked,
`succeeded` dispatch) exposed via audit log and an `attention_required`
notification. See `docs/SPRINT_5F.md`'s "Known state-model limitation"
section for the full rationale and the deliberate trade-off this
represents.

### Agent routing

`_JOB_TYPE_ROUTES` in `worker.py` is an explicit, static
`job_type -> (agent_id, capability)` table, validated against the real
`AgentRegistry.validate_capability()` before every dispatch. A job type
absent from the table fails closed with `UnknownJobRouteError` rather than
falling back to any agent.

### Retry, idempotency, and correlation

Retryability is read from `app/dispatch/errors.py`'s typed
`DispatchError.retryable` attribute — not from status-string matching.
`correlation_id` is `night_shift_job:<job_id>` (stable across every retry
of the same job); `idempotency_key` is
`night_shift_job:<job_id>:dispatch:<attempt_count>` (stable for a replay of
the same attempt, distinct for a genuine subsequent retry). A retryable
failure never rewrites `status` — it clears the lease and advances
`attempt_count`/`eligible_after` only, and the job becomes re-eligible once
both conditions are satisfied.

### Stale-lease recovery

`recover_stale_job()` inspects the real linked dispatch's current status
(`DispatchService.get_dispatch(...)`) before deciding an outcome. A
dispatch that actually `succeeded` is reconciled to `draft_saved` without
being retried; `failed`/`timed_out` follow the normal retry/fail policy;
`cancelled`/`rejected` become terminal; `awaiting_approval` is left alone
(the lease is released, but the job is not failed or retried); an
in-flight (`dispatching`/`running`) dispatch is cancelled via the canonical
`DispatchService.cancel_dispatch()` first. Recovery is idempotent —
repeated calls on an already-recovered job are no-ops.

### Cancellation

`/nightqueue cancel <job_id>` (and `NightShiftWorker.cancel_job()`
directly) cancels a job in any non-terminal state, cancelling its linked
dispatch (which transitively cancels any open approval) through the
canonical `DispatchService.cancel_dispatch()`, then transitions the job to
`failed_safely` — matching 5A.1's precedent that Night Shift has no
separate `cancelled` job status. Cancelling an already-terminal job raises
`NightJobTerminalError`.

### Notifications and the morning brief

No second notifier exists. Every worker-originated outcome routes through
the existing `NightShiftService.notify()`/`record_notification_event()`,
using `severity="attention_required"` (or `"critical"`, via the
pre-existing `essential_service_repeated_failure` critical event type, only
when retries are exhausted). `generate_morning_brief()`'s query shape is
unmodified; verified directly that a real run producing a successful
automation, an exhausted failure, and a prohibited-job rejection shows all
three in the brief's existing sections without any change to that method.

### Logging

No log statement in `app/nightshift/worker.py` or the Night Shift section
of `app/telegram_bot.py` includes a raw exception message, `repr()`, or a
traceback — only a fixed operation name, the job's own `job_id`, and a
sanitized category (an exception class name). Verified with a test that
injects a secret-shaped token and a filesystem path into an exception and
confirms neither appears in captured log output.
