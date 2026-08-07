# Sprint 5F — Full Night Shift Automation

## Status: Proposed (architecture, not yet implemented)

## Governing contract

This sprint is implemented and reviewed against
`docs/WAVE_3_INTEGRATION_CONTRACT.md`, which is the binding source for all
interfaces, DTOs, schema, state machines, and file-ownership rules
referenced below. This document does not duplicate that content — it states
5F's objective, scope, and acceptance criteria, and points to the contract's
numbered sections for anything defined there.

## Dependency

5F consumes `app/dispatch/` (`DispatchService`, `ApprovalService`,
`AgentRegistry`) owned by Sprint 5B.1. Per contract §14, 5F's branch is
implemented in parallel against the Wave 2 baseline, then **revalidated
against the actual merged 5B.1 interface** after 5B.1 merges and before 5F
itself merges. 5F must not fork, stand in for, or duplicate any part of
`app/dispatch/`.

## Objective

Give Night Shift the executor it has never had. `NightShiftService.tick()`
(Sprint 5A.1) today only flips `runtime_mode_state` and triggers the morning
brief; `register_job_executor()` validates and discards; nothing calls
`tick()` on a schedule yet. This sprint builds the first real
claim-execute-record loop, routes every job through Sprint 5B.1's dispatch
and approval interfaces, and wires it into the running bot process.

## Scope

1. **`NightShiftWorker`** (`app/nightshift/worker.py`) — `list_eligible_jobs`,
   `claim_job`, `execute_via_dispatch`, `record_result`,
   `defer_for_approval`, `recover_stale_job`. Contract §2, §8.
2. **Scheduler wiring** — register `NightShiftService.tick()` (already
   written in 5A.1, never called on a schedule) via
   `application.job_queue.run_repeating(...)` in `build_application()`, and
   drive the worker's claim loop from the same tick.
3. **Job-level lease and retry state** — four additive columns on
   `night_queue_jobs` (`dispatch_id`, `lease_worker_id`, `lease_expires_at`,
   `attempt_count`), contract §9.
4. **Retry/backoff policy** — bounded re-queue via `eligible_after`, capped
   `max_attempts`, contract §8.
5. **Prohibited-set hardening** — additive entries to
   `app/nightshift/classifier.py`'s `PROHIBITED` set: `git_merge`,
   `git_reset`, `git_rebase`, `telegram_outbound_message` (contract §6).
6. **Telegram surface**: `/nightshift`, `/nightstatus`, `/nightqueue`,
   `/wake` — reserved-but-unimplemented since 5A.1/5B, finally wired here.
7. **Quiet-hours and morning-brief integration** — no change to the
   mechanism (`record_notification_event`, `generate_morning_brief`); this
   sprint ensures dispatch-originated outcomes populate the existing
   sections correctly.

## Out of scope

- No change to `app/dispatch/`'s public interface. If 5F discovers a gap
  during implementation, it is raised to 5B.1 (the integration owner,
  contract §1) rather than worked around locally.
- No second dispatch or approval system. `app/nightshift/worker.py` contains
  no direct SQL access to `dispatches`, `dispatch_attempts`, `approvals`, or
  `approval_audit` — enforced by a structural test (contract §8, §13).
- No real model call — Night Shift dispatches through whatever adapter
  `AgentRegistry`/`DispatchService` resolve to (the one deterministic
  adapter shipped in 5B.1); 5F does not add or select adapters itself.
- No multi-worker/multi-process concurrency — one `NightShiftWorker`
  instance, one process, bounded per-tick claim batch (default 3).
- No change to `JOB_TRANSITIONS` (5A.1's `night_queue_jobs.status` state
  machine) — the four new columns are additive linkage, not a redefinition
  of Night Shift's own states.
- No change to `app/control_tower/`, `app/execution/`, or
  `app/google_workspace/`.

## Deliverables

- `app/nightshift/worker.py` (new).
- Additive edits to `app/nightshift/schema.py` (four columns + two
  indexes, guarded `ALTER TABLE` pattern), `classifier.py` (four new
  `PROHIBITED` entries), `models.py`/`repository.py` (fields and queries
  for the new columns and lease lookups).
- `tests/test_nightshift_worker.py`, `test_nightshift_automation.py`,
  `test_nightshift_schema_migration.py`, `test_telegram_nightshift.py`.
- One additive block in `app/main.py` (contract §12, insertion point 4,
  after merged 5B.1's dispatch/approval/control_tower blocks).
- Additive changes to `app/telegram_bot.py`, appended after 5B.1's block:
  `build_application()` signature (`night_worker` param), `bot_data` entry,
  `_night_worker` accessor, `job_queue.run_repeating` registration, four new
  command handlers, extended `HELP_MESSAGE`.
- `docs/SPRINT_5F.md` (this file), `docs/night-shift-runtime.md` extended
  with an automation section, `docs/CURRENT_SPRINT.md` updated with 5F's
  Wave 3 entry (appended after 5B.1's, per contract §12).

## Security constraints

Full list in contract §11. The ones specific to this sprint's own code
surface: `NightShiftWorker` never bypasses `AgentRegistry`/`ApprovalService`
policy (contract §6) — Night Shift's own `PROHIBITED` classifier set is an
**additional** floor beneath 5B.1's dispatch-level policy, not a
replacement for it; a job never blocks-and-waits on an approval overnight
(deferred jobs release their lease immediately, contract §8); stale leases
are recovered, never silently abandoned or force-completed; no lease is
force-cleared on shutdown (contract §8's shutdown behavior).

## Tests

Full list in contract §13 ("Sprint 5F"). Summary: eligibility filtering,
approval deferral (lease released, never dispatched directly), claim CAS and
duplicate-claim rejection, proof that `execute_via_dispatch` only calls into
`app/dispatch/` (structural grep test), retry/backoff re-queue timing,
timeout handling, cancellation cascading into `DispatchService.cancel_dispatch`,
stale-lease recovery across a simulated restart, per-job failure isolation
within a tick, quiet-hours severity routing (unchanged logic, new callers),
morning-brief section accuracy, shutdown/restart lease behavior, a full-suite
assertion that no `PROHIBITED`-classified job type ever reaches
`execute_via_dispatch`, the "no duplicate dispatch system" structural test,
and full regression (392 pre-Wave-3 + 5B.1 + 5F, all green together).

## Acceptance criteria

- [ ] `NightShiftWorker` implements every method in contract §2/§8 with the
      exact signatures, DTOs, and errors specified there, calling into the
      **merged, real** `app/dispatch/` (not a local stand-in) by the time
      this sprint merges.
- [ ] `NightShiftService.tick()` is registered on a real schedule in
      `build_application()` for the first time since it was written in
      Sprint 5A.1.
- [ ] Four new `night_queue_jobs` columns applied idempotently via the
      guarded `ALTER TABLE` pattern; no existing column altered;
      `JOB_TRANSITIONS` unchanged.
- [ ] `app/nightshift/classifier.py`'s `PROHIBITED` set includes
      `git_merge`, `git_reset`, `git_rebase`, `telegram_outbound_message`
      in addition to its existing entries.
- [ ] A job classified `PROHIBITED` can never reach `execute_via_dispatch`
      — verified by test, not just by classification lookup.
- [ ] `/nightshift`, `/nightstatus`, `/nightqueue`, `/wake` are registered
      exactly once each.
- [ ] All 392 pre-Wave-3 tests, all Sprint 5B.1 tests, and all new Sprint 5F
      tests pass together on `main` post-merge.
- [ ] `app/dispatch/`, `app/control_tower/`, `app/execution/`,
      `app/google_workspace/` are untouched by this sprint's diff.

## Known limitations

- Single-process, single-worker execution only — no distributed claim
  coordination beyond the SQLite CAS lease; sufficient for NOVA's current
  single-instance deployment, not designed to scale beyond it without a
  follow-up sprint.
- The one deterministic adapter shipped in 5B.1 means every "executed"
  overnight job is simulated, not a real model call — Night Shift's
  automation is real (claim, lease, retry, approval-routing, notification),
  its work product is not yet.
- Backoff is a fixed delay (default 300s), not exponential — sufficient for
  a bounded overnight window with `max_attempts=3`; a longer-running or
  higher-volume job source would need a follow-up sprint to add exponential
  backoff.
- `dispatch_leases` (5B.1's table, contract §9) is not used by this sprint —
  Night Shift's lease is job-level (`night_queue_jobs`), not dispatch-level;
  this is a deliberate scope boundary, not an oversight.
