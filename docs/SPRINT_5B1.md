# Sprint 5B.1 — Agent Dispatch & Approval Operations

## Status: Proposed (architecture, not yet implemented)

## Governing contract

This sprint is implemented and reviewed against
`docs/WAVE_3_INTEGRATION_CONTRACT.md`, which is the binding source for all
interfaces, DTOs, schema, state machines, and file-ownership rules
referenced below. This document does not duplicate that content — it states
5B.1's objective, scope, and acceptance criteria, and points to the
contract's numbered sections for anything defined there.

## Objective

Give NOVA a single, controlled way to dispatch work to a named agent and
gate any dispatch that requires a human decision behind an explicit,
auditable approval — replacing the three independent, partial approval
shapes that exist today (`ExecutionService.approve()`,
`NightShiftService.transition_night_job()`, and Control Tower's
read-only `list_approvals()` aggregation) with one canonical authority that
those and future sprints build on, instead of a fourth parallel one.

## Scope

1. **`DispatchService`** — `create_dispatch`, `dispatch`, `get_dispatch`,
   `list_dispatches`, `cancel_dispatch`, `retry_dispatch`,
   `synchronize_status`. Contract §2.
2. **`ApprovalService`** — `request_approval`, `approve`, `reject`,
   `get_approval`, `list_pending`, `expire_or_close`. Contract §2.
3. **`AgentRegistry`** — static allowlist of the eight agents listed in
   contract §7, capability validation, no runtime registration API.
4. **One local, deterministic `AgentAdapter`** (`LocalDeterministicAgentAdapter`)
   — reuses `app/execution/adapter.py`'s simulation pattern; no real model
   call in this sprint.
5. **Schema**: `dispatches`, `dispatch_attempts`, `approvals`,
   `approval_audit`, `dispatch_audit_log`, `dispatch_leases`. Contract §9.
6. **Telegram surface**: `/dispatch`, `/dispatches`, `/dispatchstatus`,
   `/approve`, `/reject`, `/canceldispatch`, `/retrydispatch`.
7. **One pre-approved, narrow edit** to `app/control_tower/service.py`:
   `ControlTowerService.__init__` gains an optional `approvals:
   ApprovalService | None = None` keyword param, and `list_approvals()`
   unions a third source (`self.approvals.list_pending()`) alongside its
   existing execution/night_shift aggregation — additive, same shape as the
   existing two sources, no change to Control Tower's own state machine or
   tables.
8. **Populate `docs/AGENT_REGISTRY.md`**'s "Active agents" section (currently
   empty since Sprint 1.1) using that document's own required-fields
   template, for the eight agents in contract §7.

## Out of scope

- No real model call (Claude/Codex/Gemini) — one deterministic adapter only,
  contract §7's "how models fit behind adapters" section describes the seam
  a *future* sprint uses, not something 5B.1 implements.
- No Google Drive/Calendar write capability — both remain read-only;
  `AgentRegistry` classifies Drive/Calendar writes as approval-required
  policy only, no such capability is actually callable.
- No Night Shift worker/scheduler code — that is Sprint 5F's exclusive
  scope; 5B.1 exposes the interfaces 5F consumes and does not itself decide
  when or whether a night job runs.
- No change to `app/execution/`'s `executions` table or `ExecutionService` —
  dispatch is a parallel, separate identifier space (contract §3 design
  note).
- No multi-approver model — single authorized user, matching
  `ExecutionService.approve()`'s existing check exactly.
- No automatic approval under any condition, including `expire_or_close`
  (contract §6).

## Deliverables

- `app/dispatch/__init__.py`, `service.py`, `approvals.py`, `registry.py`,
  `models.py`, `repository.py`, `schema.py`, `errors.py`, `adapters.py`.
- `tests/test_dispatch_schema.py`, `test_dispatch_repository.py`,
  `test_dispatch_service.py`, `test_approval_service.py`,
  `test_agent_registry.py`, `test_dispatch_security.py`,
  `test_telegram_dispatch.py`.
- One additive block in `app/main.py` (contract §12, insertion point 2) and
  one additive kwarg on the existing `control_tower` construction (insertion
  point 3).
- Additive changes to `app/telegram_bot.py`: `build_application()` signature,
  `bot_data` entries, `_dispatch_svc`/`_approval_svc` accessors, seven new
  command handlers, extended `HELP_MESSAGE`.
- `docs/SPRINT_5B1.md` (this file), `docs/agent-dispatch-and-approvals.md`
  (operational reference, mirroring `docs/executive-control-tower.md`'s
  style), `docs/AGENT_REGISTRY.md` populated, `docs/CURRENT_SPRINT.md`
  updated with the Wave 3 / Sprint 5B.1 entry.

## Security constraints

Full list in contract §11. The ones specific to this sprint's own code
surface: `payload_ref` is a hash/label only, never raw instruction content;
every free-text field is `SENSITIVE_CONTENT_PATTERN`-guarded; no
`subprocess`/shell/network call exists anywhere in
`LocalDeterministicAgentAdapter`; `AGENT_REGISTRY` never includes
`git_mutation`/`paid_action` in any agent's capability set, so no capability
even exists for `create_dispatch()` to approve its way around; every error
message is a fixed, sanitized string.

## Tests

Full list in contract §13 ("Sprint 5B.1"). Summary: dispatch creation,
idempotency (replay-safe on matching content, `DuplicateDispatchError` on
mismatched content), agent/capability validation, approval request/approve/
reject with authorization enforcement, forbidden-action rejection before
approval logic runs, cancellation from every non-terminal state, retry up to
`max_attempts`, CAS-based status sync, Telegram command registration
(each exactly once), error-message sanitization against the existing
`_SENSITIVE_INPUTS` parametrized list, migration idempotency
(`initialize()` twice), audit-row-per-state-change integrity, and a
subprocess/`os.system`-monkeypatch test proving no external call exists.

## Acceptance criteria

- [ ] `DispatchService`, `ApprovalService`, `AgentRegistry` implement every
      method in contract §2 with the exact signatures, DTOs, and errors
      specified there.
- [ ] All six new tables created idempotently (contract §9); no existing
      table or column altered.
- [ ] `ControlTowerService.list_approvals()` includes pending approvals from
      `ApprovalService` when `approvals` is injected, with no change to its
      existing execution/night_shift sources.
- [ ] `docs/AGENT_REGISTRY.md`'s "Active agents" table is populated with all
      eight agents and their capability allowlists.
- [ ] No agent capability grants git mutation, secret change, or paid
      action — verified by a test asserting these strings are absent from
      every entry in `AGENT_REGISTRY`.
- [ ] Zero automatic approval path exists — verified by a test asserting no
      code path other than `approve()` with a matching authorized user ID
      can produce `status='approved'`.
- [ ] All 392 pre-Wave-3 tests plus all new Sprint 5B.1 tests pass.
- [ ] `app/nightshift/`, `app/execution/`, `app/google_workspace/` are
      untouched by this sprint's diff.

## Known limitations

- Only one adapter (`LocalDeterministicAgentAdapter`) exists after this
  sprint — every dispatch is simulated, not a real model call. A future
  sprint wires a real adapter behind the `AgentAdapter` Protocol (contract
  §7) without changing `DispatchService`'s public interface.
- `dispatch_leases` is created but not populated by any code in this
  sprint — it exists so a future multi-worker scenario does not require
  another schema migration; the operative lease for Wave 3 is Night Shift's
  job-level lease (Sprint 5F, contract §9).
- `ApprovalService` supports exactly one authorized approver, matching the
  rest of the codebase's current single-user model — a multi-approver or
  role-based approval model is out of scope until NOVA supports more than
  one authorized Telegram user anywhere.
- Control Tower's `list_approvals()` union is display-only, same as its
  existing two sources — it does not let a user approve/reject *through*
  Control Tower; `/approve`/`/reject` operate on `approval_id`s obtained via
  `/dispatches` or `/approvals`.
