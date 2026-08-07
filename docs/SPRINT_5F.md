# Sprint 5F — Full Night Shift Automation

## Status: Implementation under review

## Governing contract

This sprint is implemented and reviewed against
`docs/WAVE_3_INTEGRATION_CONTRACT.md`, which is the binding source for all
interfaces, DTOs, schema, state machines, and file-ownership rules
referenced below. This document does not duplicate that content — it states
5F's objective, scope, and acceptance criteria, and points to the contract's
numbered sections for anything defined there.

## Dependency

5F consumes the real, merged `app/dispatch/` (`DispatchService`,
`ApprovalService`, `AgentRegistry`) owned by Sprint 5B.1 (merged at
`8c6f64a`). `app/dispatch/` is untouched by this sprint's diff
(`git diff -- app/dispatch` is empty) — 5F imports and calls it, and owns no
dispatch/approval state of its own.

## Objective

Give Night Shift the executor it has never had. `NightShiftService.tick()`
(Sprint 5A.1) previously only flipped `runtime_mode_state` and triggered the
morning brief; nothing called `tick()` on a schedule. This sprint builds a
real claim-execute-record loop, routes every job through Sprint 5B.1's
dispatch and approval interfaces, and wires it into the running bot process
via `python-telegram-bot` 22.8's `JobQueue`.

## Scope

1. **`NightShiftWorker`** (`app/nightshift/worker.py`) — `list_eligible_jobs`,
   `claim_job`, `execute_via_dispatch`, `defer_for_approval`,
   `recover_stale_job`, `cancel_job`. Every status write goes through
   `NightShiftService.transition_night_job()`; the worker itself contains no
   `UPDATE night_queue_jobs SET status = ...` anywhere (structurally
   verified by a test that greps the source, and behaviourally verified by
   a test that replays the same transition sequence directly through
   `NightShiftService` and confirms none of it is ever rejected).
2. **Scheduler wiring** — `NightShiftService.tick()` and the worker's
   claim/execute/recover loop are registered exactly once via
   `application.job_queue.run_repeating(_nightshift_tick, interval=300,
   first=10, data={...})` in `build_application()`. The callback reads
   `context.job.data` (the correct accessor on the installed PTB 22.8 — see
   "Known limitation, now fixed" below). A non-blocking `threading.Lock`
   causes an overlapping tick to skip cleanly rather than run concurrently
   with one still in progress. Each eligible job and each stale-recovery
   item is wrapped independently so one failure never stops the rest.
3. **Job-level lease and retry state** — five additive columns on
   `night_queue_jobs` (`dispatch_id`, `lease_worker_id`, `lease_expires_at`,
   `attempt_count`, `approval_id`), applied via a `PRAGMA table_info(...)`
   existence check before each `ALTER TABLE ... ADD COLUMN` — never a blind
   `try/except sqlite3.OperationalError: pass`. `approval_id` persists the
   real `ApprovalService.request_approval()` result as a structured column,
   not only as audit-log text.
4. **Retry/backoff policy** — a retryable failure never rewrites `status`;
   it only clears the lease and sets `attempt_count`/`eligible_after` via
   dedicated metadata-only repository methods, and the job is re-selected
   once its lease is free and `eligible_after` has passed. Retryability is
   read from the real `app/dispatch/errors.py` typed exceptions'
   `.retryable` attribute, not from ad hoc status-string matching.
5. **Prohibited-set hardening** — additive entries to
   `app/nightshift/classifier.py`'s `PROHIBITED` set: `git_merge`,
   `git_reset`, `git_rebase`, `telegram_outbound_message` (contract §6).
   A `PROHIBITED` job discovered still queued (defensive — the normal
   `enqueue_night_job()` path already refuses these before insertion) is
   transitioned directly to `rejected` (a legal `queued → rejected` edge)
   and reported via an `attention_required` notification, rather than left
   invisible in the queue forever.
6. **Telegram surface**: `/nightshift`, `/nightstatus`, `/nightqueue`
   (queue visibility plus `/nightqueue cancel <job_id>`), `/wake`.
7. **Quiet-hours and morning-brief integration** — no change to
   `generate_morning_brief()`'s query shape or to
   `record_notification_event()`. Every worker-originated outcome
   (successful automation, retry scheduled, failed safely, prohibited
   rejection, retry-exhausted) is reported through the existing
   `NightShiftService.notify()` with `severity="attention_required"` (or
   `"critical"` for retry-exhausted, using the pre-existing
   `essential_service_repeated_failure` critical event type), which the
   unmodified brief already surfaces in its attention-required section.
   Verified directly: a real end-to-end run producing a successful
   automation, an exhausted failure, and a prohibited rejection shows all
   three in `NightShiftService.generate_morning_brief()`'s actual output
   with zero changes to that method.

## Out of scope

- No change to `app/dispatch/`'s public interface or any file under it.
- No second dispatch or approval system — `app/nightshift/worker.py`
  contains no direct SQL access to `dispatches`, `dispatch_attempts`,
  `approvals`, or `approval_audit` (structural test) and no fake/stand-in
  dispatch or approval class (structural test).
- No real model call — dispatches through whatever adapter
  `AgentRegistry`/`DispatchService` resolve to.
- No multi-worker/multi-process concurrency.
- **No change to `JOB_TRANSITIONS`** (5A.1's `night_queue_jobs.status` state
  machine) — `git diff main -- app/nightshift/service.py` is empty. See
  "Known state-model limitation" below for how this sprint represents
  outcomes the original transition graph cannot directly express, without
  weakening it.
- No change to `app/control_tower/`, `app/execution/`, or
  `app/google_workspace/`.

## Known state-model limitation (read before touching `worker.py`)

5A.1's `JOB_TRANSITIONS` only reaches `completed` via `awaiting_approval`,
which requires a real human decision (`ApprovalService.approve()`). An
approval-free automated job whose dispatch succeeds has no legal transition
directly to `completed` — and this sprint will not fake human approval or
modify the shared state machine to invent one. Instead, such a job is
advanced through **only legal, existing transitions**
(`preparing → validating → draft_saved`) to `draft_saved` — the state 5A.1
already defines as "the draft/work is ready" — and the real outcome
(a linked `dispatch_id` whose `DispatchService.get_dispatch(...).status`
is `succeeded`) is exposed via `night_shift_audit_log` and an
`attention_required` notification, both visible in `/nightqueue`,
`/nightstatus`, and the morning brief. This is a deliberate, documented
trade-off, not an oversight: closing this gap for real (letting fully
automated work reach `completed`) is future-sprint scope requiring a
product decision about whether/how much unattended automation should ever
reach a "done" state without a human step, which is out of bounds for a
sprint whose explicit constraint is "do not weaken approval semantics for
convenience."

The same constraint applies to `defer_for_approval`: `awaiting_approval` is
only legally reachable from `queued` or `draft_saved`, never from
`preparing`/`validating` directly, so a job that becomes approval-required
after being claimed is first legally advanced to `draft_saved` (exactly as
above) before the `draft_saved → awaiting_approval` transition is applied —
using only existing, already-defined edges.

## Deliverables

- `app/nightshift/worker.py` (new).
- Additive edits to `app/nightshift/schema.py` (five columns + two indexes,
  `PRAGMA table_info(...)`-guarded), `classifier.py` (four new `PROHIBITED`
  entries), `models.py` (new fields), `repository.py` (lease/dispatch/
  approval/attempt metadata-only methods — none of which write `status`).
- `tests/test_nightshift_worker.py`, `tests/test_nightshift_schema_migration.py`,
  `tests/test_telegram_nightshift.py`. (`test_nightshift_automation.py` from
  the original plan was folded into `test_nightshift_worker.py` rather than
  kept as a separate file — the split added no independent coverage.)
- One additive block in `app/main.py` constructing `NightShiftWorker` from
  the already-constructed 5B.1 services (no duplicate `DispatchService`/
  `ApprovalService`/`AgentRegistry`).
- Additive changes to `app/telegram_bot.py`: `build_application()` signature
  (`night_worker` param), `bot_data["night_worker"]`, four new command
  handlers, `job_queue.run_repeating` registration.
- `docs/SPRINT_5F.md` (this file), `docs/night-shift-runtime.md` (extended
  with an automation section), `docs/CURRENT_SPRINT.md` (updated).

## Security constraints

`NightShiftWorker` never bypasses `AgentRegistry`/`ApprovalService` policy;
every dispatched job's `(agent_id, capability)` comes from an explicit,
static routing table (`_JOB_TYPE_ROUTES`) validated against the real
`AgentRegistry.validate_capability()`, not a category-matching heuristic
with a silent universal fallback. A job type absent from the table fails
closed (`UnknownJobRouteError`) rather than dispatching anywhere. No log
statement anywhere in `app/nightshift/worker.py` or the Night Shift section
of `app/telegram_bot.py` interpolates a raw exception, its message,
`repr()`, or a traceback — only a fixed operation name, the job's own
`job_id`, and a sanitized category (an exception's class name, never its
message) are logged. Verified with a `caplog`-based test that injects an
exception containing a secret-shaped token and a filesystem path and
asserts neither appears in the captured log text. A job never blocks-and-
waits on an approval overnight (deferred jobs release their lease
immediately). Stale leases are recovered by inspecting the real linked
dispatch's current status first — a dispatch that actually succeeded is
reconciled without being retried or discarded; only a lease still tied to a
dispatch that is genuinely failed/timed-out/missing is treated as
retryable.

## Tests

`tests/test_nightshift_worker.py` (42 tests) and `tests/test_telegram_nightshift.py`
(10 tests) exercise, against real `NightShiftService`/`DispatchService`/
`ApprovalService`/`AgentRegistry` instances (never a fake/stand-in): the
unmodified `JOB_TRANSITIONS` shape and its match against `main`; eligibility
filtering including prohibited-job rejection and unknown-job-type
fail-closed behavior; claim CAS rejection for every terminal/waiting state
and a real two-thread claim race; the honest `draft_saved` success outcome;
approval deferral with a persisted `approval_id`; the explicit routing
table's coverage of every dispatchable `REGISTERED_JOBS` entry and its
validation against `AgentRegistry`; typed retryable/non-retryable/internal
dispatch-error handling; retry exhaustion reaching a visible terminal state;
same-attempt dispatch replay reusing the existing dispatch row; stable
root-correlation-id and distinct-per-retry idempotency-key format; stale
recovery reconciling an actually-succeeded dispatch without retrying it,
and being idempotent under repeated calls; explicit cancellation (queued,
in-flight with linked-dispatch cancellation, and terminal-state rejection);
safe-logging content verified via `caplog`; and a real, runtime execution
of the PTB scheduler callback (not source inspection) proving it does not
raise `AttributeError`, correctly retrieves `context.job.data`, isolates a
single failing job from the rest of the tick, and skips cleanly on an
overlapping invocation.

## Acceptance criteria

- [x] `NightShiftWorker` calls the real, merged `app/dispatch/` — no local
      stand-in exists anywhere in the tree.
- [x] `NightShiftService.tick()` is registered on a real schedule and the
      registered callback runs correctly against the installed PTB 22.8
      (verified by executing it, not by inspecting source text).
- [x] Five `night_queue_jobs` columns applied idempotently via the guarded
      `ALTER TABLE` pattern; no existing column altered; `JOB_TRANSITIONS`
      is byte-for-byte identical to `main`.
- [x] `app/nightshift/classifier.py`'s `PROHIBITED` set includes
      `git_merge`, `git_reset`, `git_rebase`, `telegram_outbound_message`.
- [x] A job classified `PROHIBITED` can never reach `execute_via_dispatch`
      — verified by test.
- [x] `/nightshift`, `/nightstatus`, `/nightqueue`, `/wake` are registered
      exactly once each.
- [x] `app/dispatch/`, `app/control_tower/`, `app/execution/`,
      `app/google_workspace/` are untouched by this sprint's diff.
- [ ] Full regression run and sign-off by an independent reviewer of this
      corrective pass (see "Recommendation" — this status line is left
      unchecked deliberately; it is not this document's place to certify
      its own review).

## Known limitations

- Single-process, single-worker execution only — no distributed claim
  coordination beyond the SQLite CAS lease.
- The one deterministic adapter shipped in 5B.1 means every "executed"
  overnight job is simulated, not a real model call.
- Backoff is a fixed delay (default 300s), not exponential.
- `dispatch_leases` (5B.1's table) is not used by this sprint — Night
  Shift's lease is job-level (`night_queue_jobs`), not dispatch-level.
- **Approval-free automated jobs rest at `draft_saved`, not `completed`**
  (see "Known state-model limitation" above) — this is the most significant
  remaining limitation and the one most likely to need a follow-up product
  decision: whether NOVA should ever let unattended automation reach a
  literal "done" state, and if so, how `JOB_TRANSITIONS` should be safely
  and explicitly extended (with tests distinguishing the automated path
  from the human-approval path) to support it.
- Scheduler polling interval (300s) is a fixed literal in
  `build_application()`, not read from `Settings` — acceptable for the
  current single-instance deployment, a minor follow-up item if
  configurability is ever needed.
