# Sprint 8E — Approval-Gated Workspace Actions

## Status: **FROZEN** — architecture decisions applied. Covers the active
writable Workspace surface (Gmail, Calendar, Drive, Docs, Sheets, Slides)
with the lifecycle stated exactly as specified. Revised per Control Tower
Freeze Review: **Google Keep is deferred and out of active Wave 7 scope** —
this sprint has no Keep action type, no Keep write service, and no
Keep-related capability gating. This remains Wave 7's highest-risk sprint;
every constraint below is load-bearing, not advisory.

See `docs/WAVE_7_SHARED_CONTRACTS.md` §3.7, §3.8, §4 (AD-W7-04, AD-W7-05,
AD-W7-14), §5a, §5b, §7, §9 for the cross-sprint contract this spec
implements.

## 1. Objective

Let NOVA actually perform a real external Google Workspace write — across
Gmail, Calendar, Drive, Docs, Sheets, and Slides — but only after explicit,
unambiguous human approval, with replay protection and full auditability.

## 2. User-visible usable capability

Once a `PreparedWorkspaceAction` (8C) is ready, or a structured action
(calendar event, file share) is specified directly, the user can request
NOVA execute it for real. NOVA shows the exact recipient/resource/action
before asking for approval, requires an explicit numbered confirmation
(never a bare "oke"), executes exactly once even under retry or duplicate
Telegram delivery, and records what happened — never message content or
credentials — in an audit trail.

## 3. Scope

- New `app/workspace_actions/` package: `models.py`, `schema.py`,
  `repository.py`, `service.py`.
- New write-capability files inside the already-merged
  `app/google_workspace/` package (additive, new files only — AD-W7-03):
  `gmail/write_service.py` (`create_draft()`, `send()`, `reply()`),
  `calendar/write_service.py` (`create_event()`, `update_event()`),
  `drive/write_service.py` (`share_file()`), `docs/write_service.py`
  (`create_document()`, `edit_document()`), `sheets/write_service.py`
  (`write_range()`), `slides/write_service.py` (`create_presentation()`,
  `update_presentation()`). Each mirrors its read sibling's DTO/exception/
  audit discipline. **No `keep/write_service.py` exists.**
- Extend `app/dispatch/registry.py`: add `external_communication` to
  `workspace_agent`'s capability frozenset; change its `adapter_id` from
  `local_deterministic` to `google_workspace_adapter`. No other agent's
  entry changes.
- Extend `app/dispatch/adapters.py`: new `GoogleWorkspaceAgentAdapter`
  (`get_adapter("google_workspace_adapter")`), whose `execute(dispatch,
  attempt)` reads the `WorkspaceAction` row referenced by `payload_ref`,
  calls the matching write-service method, and returns a `DispatchResult`
  — invoked by `DispatchService.dispatch()` only after the backing
  `DispatchRecord` reaches `approved`.
- `WorkspaceActionService`:
  - `request_content_action(action_type, prepared_action_id, actor,
    idempotency_key, correlation_id=None) -> WorkspaceAction` — for the
    **content actions** (§4 of the shared contract's §3.8): reads the
    referenced `PreparedWorkspaceAction` via 8C's `get_ready_action()`
    (fails closed if not `ready_for_action`), builds `resource_summary`
    from it.
  - `request_structured_action(action_type, parameters, actor,
    idempotency_key, correlation_id=None) -> WorkspaceAction` — for the
    three **structured actions** (`create_calendar_event`,
    `update_calendar_event`, `share_file`): validates `parameters` directly
    (time/title/attendees; file ID/permission), no `PreparedWorkspaceAction`
    involved.
  - Both variants converge on the same internal `_create(...)` that
    persists the `WorkspaceAction` row (`status='prepared'`, matching the
    task-specified lifecycle exactly) then calls
    `DispatchService.create_dispatch(...)` with
    `capability="external_communication"`, `agent_id="workspace_agent"`,
    `payload_ref=f"workspace_action:{action.id}"`,
    `idempotency_key=idempotency_key` — **the only code path allowed to
    call `DispatchService.create_dispatch()`/`ApprovalService.
    request_approval()` on behalf of a Workspace action** (mirrors 7D's
    `start_execution()` exclusivity).
  - `execute_after_approval(action_id, actor) -> WorkspaceAction` — called
    only after `ApprovalService.approve()` has already transitioned the
    backing dispatch to `approved`; calls `DispatchService.dispatch()`,
    which invokes the adapter. `DispatchService.dispatch()` itself already
    refuses a non-`approved` dispatch (`ApprovalRequiredError`) — a second,
    structural enforcement layer beyond convention.
- New Telegram commands: `/workspaceactions` (list, read-only),
  `/workspaceaction <id>` (detail — exact recipient/resource/action visible
  before any approval step), plus reuse of the **existing** `/approve`/
  `/reject` commands (5B.1/7D) — 8E invents no new approve/reject command.
- New setting: `NOVA_WORKSPACE_WRITES_ENABLED` (default `false`), a
  defense-in-depth kill switch `request_content_action()`/
  `request_structured_action()` check first, refusing to create even a
  pending action when unset.

## 4. Lifecycle (frozen, exact states — no extra states invented)

```
prepared -> awaiting_approval -> approved -> executing -> succeeded
                                                        -> failed
                              -> (approval) -> rejected
prepared / awaiting_approval / approved -> cancelled   (explicit user cancel only)
```

`prepared` here is `WorkspaceAction`'s own initial state (distinct from 8C's
*separate* `PreparedWorkspaceAction.status='prepared'` — the two are
different objects with a naming coincidence flagged explicitly in §8 to
avoid confusion during implementation). `cancelled` reuses
`DispatchService.cancel_dispatch()`'s existing transition rather than
inventing new dispatch-layer machinery.

## 5. Out of scope

- **Google Keep, in any form.** Per Control Tower directive
  (`docs/WAVE_7_SHARED_CONTRACTS.md` §6, AD-W7-14), Keep is out of active
  Wave 7 scope entirely; 8E has no Keep action type, no Keep write service,
  and performs no Keep capability check of any kind. This is a hard
  exclusion, not a deferred detail.
- Delete/destructive operations of any kind — not implemented in Wave 7 at
  all (shared contract §7's risk matrix). `action_type`'s closed vocabulary
  (§8) has no delete value for any of the six active services.
- Any implicit or contextual approval — every `WorkspaceAction` approval
  goes through `ApprovalService.approve()`, the one Wave 7 integration point
  with 8G's HIGH-risk confirmation discipline (§9).
- Bulk/batch actions — v1 is one action at a time, each with its own
  idempotency key and approval.
- Automatic retry of a failed external write (§9).

## 6. Existing architecture reused

- `app.dispatch.service.DispatchService` — the entire state machine,
  idempotency (`find_idempotent`, `DuplicateDispatchError`), and
  retry-exhaustion accounting — reused verbatim.
- `app.dispatch.approvals.ApprovalService` — the entire approve/reject
  authority, including its existing single-authorized-user check — reused
  verbatim.
- `app.dispatch.registry.AgentRegistry.validate_capability()` — reused
  unchanged; only `workspace_agent`'s capability set is extended.
- `app.google_workspace.{auth,factory}` — `GoogleClientFactory` reused for
  write-service client construction exactly as for read services.
- 8C's `DraftingService.get_ready_action()` / 8D's equivalent narrow read
  method — read via each package's own narrow method, never direct SQL.

## 7. Owned files/modules

- `app/workspace_actions/{models,schema,repository,service}.py` — new,
  8E-exclusive.
- `app/google_workspace/{gmail,calendar,drive,docs,sheets,slides}/
  write_service.py` — new files, additive to an already-merged package
  (AD-W7-03); no existing file in `app/google_workspace/` is modified.
  **No `keep/` write service exists.**
- `app/dispatch/registry.py` — one capability added to one existing
  `RegisteredAgent`, one `adapter_id` changed. The **only** Wave 7 edit to
  a Wave-6-frozen file, 8E-exclusive (AD-W7-04).
- `app/dispatch/adapters.py` — one new adapter class, one new
  `get_adapter()` mapping entry. 8E-exclusive.
- `app/config.py`, `.env.example` — one additive setting,
  `NOVA_WORKSPACE_WRITES_ENABLED`.
- `docs/AGENT_REGISTRY.md` — additive documentation note on
  `workspace_agent`'s new capability, mirroring 7D's own precedent (no
  table restructuring).
- `app/telegram_bot.py` — one additive block; one additive
  `build_application()` parameter,
  `workspace_actions: WorkspaceActionService | None = None` (Stage 4, last
  append — no ordering contention, since 8E is the sole Stage 4 sprint).
- `app/main.py` — one additive construction block, last in construction
  order.
- `tests/test_workspace_actions_*.py` — new, 8E-exclusive.

## 8. Data/contracts

New additive table, owned entirely by `app/workspace_actions/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS workspace_actions (
    id                     INTEGER PRIMARY KEY,
    action_kind            TEXT NOT NULL CHECK (action_kind IN ('content','structured')),
    action_type            TEXT NOT NULL CHECK (action_type IN
                              ('create_gmail_draft','send_email','reply_email',
                               'create_docs_file','edit_docs','write_sheets',
                               'create_slides','update_slides',
                               'create_calendar_event','update_calendar_event',
                               'share_file')),
    prepared_action_id     INTEGER REFERENCES prepared_workspace_actions(id),
    structured_parameters  TEXT,
    resource_summary       TEXT NOT NULL,
    dispatch_id            TEXT,
    status                 TEXT NOT NULL DEFAULT 'prepared'
                           CHECK (status IN
                              ('prepared','awaiting_approval','approved',
                               'executing','succeeded','failed','rejected','cancelled')),
    idempotency_key        TEXT NOT NULL,
    requested_by           TEXT NOT NULL,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_action_idem
    ON workspace_actions(action_type, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_workspace_action_status ON workspace_actions(status);
CREATE TABLE IF NOT EXISTS workspace_action_audit_log (
    id         INTEGER PRIMARY KEY,
    action_id  INTEGER NOT NULL REFERENCES workspace_actions(id) ON DELETE CASCADE,
    event      TEXT NOT NULL,
    actor      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
```

`action_kind` mechanically enforces §3's content-vs-structured split — a
`content` row always has `prepared_action_id` set and
`structured_parameters` `NULL`; a `structured` row is the reverse (validated
in the service layer, matching this repository's usual "SQLite CHECK for
enums, service-layer validation for cross-field rules" split, same as 8C
§8). `resource_summary` is the exact, bounded, human-readable text shown
before approval (e.g. "Send to org_a1b2c3d4e5f6: 'Re: Proposal timeline'" or
"Write B2:D10 in spreadsheet 'Q3 Budget'") — never full message/document
body, never a raw email address (reuses 8A's hashing convention).
`dispatch_id` is populated once `create_dispatch()` succeeds — same
"set only after real linkage exists" discipline as 7D's
`AgentAssignment.dispatch_id`. `status` mirrors `DispatchRecord.status`
values it cares about but is a local, denormalized convenience column —
`DispatchRecord` remains the single source of truth for whether execution
actually happened (§14 risk note).

`WorkspaceActionType` (closed vocabulary, `models.py`) — exactly the
**eleven** values above; no delete/destructive type exists; **no
Keep-related value exists.**

## 9. Security constraints — hard constraints, restated as implementation requirements

- **No external write without explicit approval.** Every `WorkspaceAction`
  is created with `capability="external_communication"`, which
  `DispatchService.create_dispatch()` already routes to
  `status="awaiting_approval"` and auto-creates the backing
  `ApprovalRequest`. `DispatchService.dispatch()` itself raises
  `ApprovalRequiredError` if invoked before approval — enforced twice,
  independently.
- **Approval cannot be inferred from "oke."** The Telegram flow leading to
  either `request_*` method always presents the action via
  `ConversationService.ask()` with `risk_level='high'`; per AD-W7-09, a bare
  contextual reply against a `high`-risk `PendingInteraction` is
  `ambiguous`, never resolving. Separately, `ApprovalService.approve()`
  itself requires the authorized user's explicit `/approve <approval_id>` —
  two independent explicit-confirmation layers.
- **Replay/double-send is prevented at two levels:** (1)
  `(action_type, idempotency_key)` uniqueness in `workspace_actions`; (2)
  `DispatchService.create_dispatch()`'s own `(source_type, source_id,
  idempotency_key)` uniqueness. A `WorkspaceAction` that already reached
  `succeeded` can never be re-executed — `execute_after_approval()` checks
  `status not in {'succeeded','failed','rejected','cancelled'}` before
  calling `dispatch()`, and `DispatchService.dispatch()` itself only accepts
  `pending`/`approved` dispatches.
- **Duplicate Telegram delivery cannot double-execute or double-approve.**
  `/approve <approval_id>` against an already-`approved`/non-`requested`
  approval is rejected by `ApprovalService.approve()`'s existing
  `StaleUpdateError` path (reused, not reimplemented); two concurrent
  `execute_after_approval()` calls against the same approved action are
  serialized by `DispatchService`'s existing `BEGIN IMMEDIATE` compare-and-
  swap transitions, so exactly one results in an actual adapter call.
- **No silent retry that can duplicate an external effect.** A `failed`
  `WorkspaceAction` is not auto-retried by 8E; `DispatchService.
  retry_dispatch()` exists but is never called automatically from
  `app/workspace_actions/`. A retry requires a new, explicit user-initiated
  request with a new `idempotency_key` and a fresh approval cycle.
- **Ambiguous execution outcome never becomes a false `succeeded`.** If the
  underlying write-service call's result is genuinely ambiguous (e.g. a
  network timeout after the request may have already reached Google), the
  adapter maps this to `failed` with a `resource_summary`-adjacent note
  requiring human reconciliation via `/workspaceaction <id>` — never guessed
  as success.
- **Final recipient/resource/action details are visible before approval.**
  `/workspaceaction <id>` and the `ConversationService.ask()` prompt both
  render `resource_summary` in full before any approval action is possible.
- **Audit records metadata only.** `workspace_action_audit_log.detail` never
  contains message/document body, recipient raw address, or credentials.
- **No token, credential, or OAuth value ever appears in a `WorkspaceAction`
  row, audit log, or Telegram message.**
- `NOVA_WORKSPACE_WRITES_ENABLED=false` (default) causes both `request_*`
  methods to fail closed before any dispatch/approval row is created.

## 10. Tests

- `request_content_action()`/`request_structured_action()` — valid request
  for each of the eleven `action_type` values, disabled-flag rejection (no
  row written), duplicate `idempotency_key` short-circuit, `prepared_
  action_id` not `ready_for_action` fails closed (content actions), invalid
  `parameters` fails closed (structured actions).
- `execute_after_approval()` — refuses to execute a non-`approved` action;
  succeeds only after a real `ApprovalService.approve()` call; a
  `succeeded` action cannot be re-executed; a `failed` action is not
  auto-retried.
- **Double-send test (explicit):** two concurrent `execute_after_approval()`
  calls against the same already-approved action result in exactly one
  external write attempt — verified via a fake write-service call counter.
- **Duplicate-approval test (explicit):** two concurrent `/approve` calls
  against the same approval resolve to exactly one `approved` transition,
  the second observably rejected via the existing `StaleUpdateError` path.
- `GoogleWorkspaceAgentAdapter.execute()` — success, provider failure
  (mapped to `failed`, not raising uncaught), timeout/ambiguous-outcome
  (mapped to `failed` requiring reconciliation, never `succeeded`) — for at
  least one representative `action_type` per service.
- Structural test: `workspace_agent`'s registry entry is the only changed
  `AGENT_REGISTRY` entry.
- **Structural test:** the string `keep`/`Keep` (case-insensitive) does not
  appear as an `action_type` value, a write-service filename, or a method
  name anywhere under `app/workspace_actions/` or the new
  `write_service.py` files.
- `resource_summary`/audit content — asserts no raw email address, message/
  document body, or credential value ever appears in a persisted row or a
  rendered Telegram message.
- Telegram — `/workspaceaction <id>` renders full detail pre-approval;
  `/approve`/`/reject` correctly resolve a Workspace action's backing
  approval; unauthorized user rejected.
- Full existing regression suite (759 passing) passes unmodified, **with
  named attention to `tests/test_dispatch_service.py`,
  `tests/test_approval_service.py`, `tests/test_agent_registry.py`** —
  regression on these three is an explicit acceptance gate, not implied by
  "full suite passes," since this is the one sprint touching a Wave-6-frozen
  file.

## 11. Acceptance criteria

1. No external write occurs without a genuine `ApprovalService.approve()`
   call by the authorized user, verified end to end (request → approval →
   execute → audit) for at least one action of each of the eleven types.
2. A bare contextual reply never triggers execution — only an explicit
   numbered `ConversationService` resolution or the existing `/approve`
   command does.
3. Re-approving or re-executing an already-`succeeded` action is
   structurally impossible (test-verified); two concurrent execution
   attempts against one approved action produce exactly one external write;
   two concurrent approval attempts produce exactly one `approved`
   transition.
4. `action_type`'s closed vocabulary contains exactly eleven values across
   six services, none of them Keep-related — verified by the structural
   `keep`-absence test.
5. Every existing `app/dispatch/**` test still passes unmodified except
   8E's own additive extension to `AGENT_REGISTRY`/`get_adapter()`.
6. `NOVA_WORKSPACE_WRITES_ENABLED=false` (the default) prevents any
   `WorkspaceAction` from being created at all, for both content and
   structured action requests.
7. Full existing regression suite (759 passing) passes unmodified.

## 12. Integration contract

Stage 4 — last to merge, after Stage 3 Integration Gate G3 passes, after 8A,
8B, 8C, 8D (all hard dependencies). Depends on 8G (Stage 1, already merged
by Stage 4) for its HIGH-risk confirmation path, though
`WorkspaceActionService` remains independently testable against a fake
`ConversationService`. 8E's own merge is the Final Wave 7 Integration Gate
G4 (`docs/WAVE_7_SHARED_CONTRACTS.md` §12) — human/Control Tower approval is
required before any commit/merge/push to `main`.

## 13. Explicit prohibited edits

- No edit to `app/dispatch/**` beyond the one named registry-capability and
  adapter-mapping addition — every transition table, every other agent's
  entry, every existing method signature in `DispatchService`/
  `ApprovalService` is unchanged.
- No delete/destructive `action_type` added to the closed vocabulary.
- **No Keep action type, Keep write service, or Keep capability check of
  any kind.**
- No edit to `app/drafting/**`, `app/workspace_bridge/**` beyond calling
  their existing narrow read methods.
- No new approval mechanism, table, or authorization check outside
  `ApprovalService`/`app.security.is_authorized_user`.
- No automatic retry path for a failed `WorkspaceAction`.

## 14. Known risks / technical debt

- `workspace_actions.status` is a denormalized convenience mirror of
  `DispatchRecord.status`; the implementer should add a structural
  consistency test asserting the two never diverge, following 7D's
  `operators.py` routing-table drift-risk precedent.
- Extending `workspace_agent`'s capabilities/`adapter_id` is, by
  construction, an edit to a file Wave 6 marked frozen *for Wave 6's own
  sprints* — AD-W7-04 documents why this is the intended way to close a gap
  Wave 6 itself flagged as open, but it remains the single highest-scrutiny
  diff in all of Wave 7 and should receive a dedicated review pass beyond
  the standard per-sprint gate.
- v1 ships no delete/destructive capability at all — a future wave adding
  one requires its own fresh architecture pass, not a quiet enum extension.
- The naming coincidence between 8C's `PreparedWorkspaceAction.status
  ='prepared'` and 8E's own `WorkspaceAction.status='prepared'` (§4) is
  flagged explicitly here so an implementer does not conflate the two
  state machines while wiring them together.
- If Google Keep is ever revisited (AD-W7-14's four conditions), a future
  sprint would add a `keep/write_service.py`, a Keep capability-gating
  check reading `WorkspaceCapabilityReport` (whatever that report's shape
  is by then), and new `action_type` values — this is explicitly a fresh,
  separately-scoped addition, not something 8E's current schema silently
  anticipates.
