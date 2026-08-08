# Sprint 7A — Executive Workflow

## Status: **FROZEN** — final ChatGPT/Control Tower architecture decisions applied.

See `docs/WAVE_6_SHARED_CONTRACTS.md` for the cross-sprint contract this
spec depends on (AD-W6-01, AD-W6-08, AD-W6-09).

**Frozen 7A ↔ 7D ownership (AD-W6-01, final):** 7A owns `WorkItem`, its
workflow, its `status`, and its `next_action` computation — exclusively.
7A never writes `agent_assignments` (7D's exclusive table) and never embeds
or duplicates `AgentAssignment` state on `WorkItem`. Where 7A displays an
owner/operator, that value is always derived at read time from 7D's frozen
`AssignmentSummary` interface (§7, §8) — never stored by 7A, and never the
canonical record of any assignment.

## 1. Objective

Turn a captured executive request into a traceable workflow: CAPTURE →
PROJECT → WORK ITEM → OWNER → STATUS → NEXT ACTION → DECISION/APPROVAL when
needed — using the existing Control Tower `WorkItem`, not a new model.

## 2. User-visible usable capability

A captured executive request becomes a `WorkItem`, optionally associated
with a Workspace Memory `Project`, with **status**, **owner**, and **next
action** all visible to NOVA (via Telegram) in one place.

## 3. Scope

- Extend `ControlTowerService` with two new computed (not persisted) reads:
  `owner_for(item: WorkItem) -> str` and
  `next_action_for(item: WorkItem) -> str`.
- Extend `/capture` to accept an optional project association
  (`/capture category title | project name`, pipe-delimited, matching the
  existing `/task project | title` grammar) — resolves `project name` to
  `project_id` via the existing, read-only
  `WorkspaceMemoryService`/`MemoryRepository` project lookup. No new Project
  write path.
- Extend `/today`'s Telegram rendering to show owner + next action per item
  (text-only change; `WorkItem` itself is unchanged).
- Add a new read-only Telegram command `/workitem <item_id>` for a single
  item's full detail (status, owner, next action, dependencies, decisions).
- Optional constructor injection: `ControlTowerService.__init__` gains an
  optional `agent_assignments: 'AgentAssignmentService | None' = None`
  parameter (mirrors the existing optional `execution`/`night_shift`/
  `approvals` parameters exactly). When present (i.e., once 7D lands and
  `app/main.py` wires it in), `owner_for()` prefers the item's latest
  `AgentAssignment.assigned_agent_id`; when absent, it falls back to the
  existing `recommended_route` field. This is why 7A does not block on 7D.

## 4. Out of scope

- Autonomous external execution of any WorkItem.
- A complex workflow engine or BPMN.
- Automatic approvals — `awaiting_approval` still requires
  `ApprovalService`/`ControlTowerService.resolve_approval()` exactly as
  today.
- Broad NLP redesign of `/capture`'s parsing — the pipe-delimited grammar is
  extended, not replaced.
- Persisting `owner`/`next_action` as new columns (AD-W6-01).

## 5. Existing architecture reused

- `app.control_tower.models.WorkItem`, `Decision`, `Approval` — unchanged.
- `app.control_tower.service.ControlTowerService` — the only thing extended
  (new methods, one new optional constructor parameter; no existing method
  signature changes).
- `app.control_tower.repository.ControlTowerRepository` — unchanged.
- `WORK_ITEM_TRANSITIONS`, `APPROVED_CATEGORIES`, `ROUTES` — unchanged,
  reused as-is for `owner_for()`'s fallback and `next_action_for()`'s status
  mapping.
- `app.memory.services.WorkspaceMemoryService` — read-only project lookup by
  name (existing method), no new Project write path.
- `app.security.SENSITIVE_CONTENT_PATTERN` — reused for the new `/capture`
  project-name field, same validation discipline as every other free-text
  field.

## 6. Owned files/modules

- `app/control_tower/service.py` (extend only — new methods + one new
  optional constructor parameter).
- `app/control_tower/service.py`'s Telegram-facing text formatting, if kept
  there, or a small new `app/control_tower/formatters.py` if formatting
  logic grows enough to warrant separation (implementer's call, additive
  either way).
- `app/telegram_bot.py` — one additive block: extended `/capture` parsing,
  extended `/today` rendering, new `/workitem` command.
- New test files: `tests/test_control_tower_owner_next_action.py`,
  extensions to `tests/test_telegram_control_tower.py` (7A's own test file;
  no other sprint edits it).

7A has exclusive ownership of `app/control_tower/**` for this wave (§4 of
the shared contract) — no other Wave 6 sprint edits any file in that
package.

## 7. Shared dependencies

- `app/memory/**` — read only (project lookup).
- `app/agent_assignment/**` — optional read dependency, wired via
  constructor injection in `app/main.py` only. 7A's own code never imports
  `app/agent_assignment/` internals directly; it calls exactly one frozen
  method on the injected service — `get_active_assignment_summary(item.item_id)
  -> AssignmentSummary | None` (`docs/SPRINT_7D.md` §3/§8) — and reads only
  `AssignmentSummary.assigned_agent_id`/`.operator_id`. This interface is
  frozen (AD-W6-01); it is no longer subject to per-merge negotiation.
- `app/main.py`, `app/telegram_bot.py` — shared, ordered append (§4/§5 of
  the shared contract).

## 8. Data/contracts

No schema changes. `owner_for()`/`next_action_for()` are pure functions of
already-persisted `WorkItem` fields (plus, optionally, injected
`AgentAssignment` reads):

```python
def next_action_for(self, item: WorkItem) -> str:
    if item.status == "clarification_needed":
        return f"Resolve clarification: {item.clarification_needs}"
    if item.status == "awaiting_approval":
        return "Awaiting approval"
    if item.status in {"inbox", "planned"}:
        if self.unresolved_blocker_count(item) > 0:
            return "Blocked on dependencies"
        return f"Assign to {item.recommended_route or 'an agent'}"
    if item.status == "in_progress":
        return "In progress"
    if item.status == "deferred":
        return "Deferred — reschedule or cancel"
    return "No action required"  # completed, cancelled
```

`owner_for()` returns `item.recommended_route` unless the injected
`agent_assignments` service's `get_active_assignment_summary(item.item_id)`
returns a non-`None` `AssignmentSummary`, in which case it returns that
summary's `assigned_agent_id` (and, where the rendering surface wants it,
`operator_id`) display name. `owner_for()` never queries
`agent_assignments` itself and never caches the returned `AssignmentSummary`
beyond the current call.

## 9. Security constraints

- New `/capture` project-name field goes through the same `_clean_text()` +
  `SENSITIVE_CONTENT_PATTERN` screening as every other Control Tower
  free-text field.
- `/workitem <item_id>` is read-only; it never returns raw dependency
  internals, SQL, or stack traces on a missing/invalid ID — matches the
  existing sanitized-error convention in `docs/executive-control-tower.md`.
- No new capability, no new approval path — `awaiting_approval` items still
  route through the unchanged `ApprovalService`.

## 10. Tests

- `owner_for()`/`next_action_for()` for every `WORK_ITEM_STATES` value,
  with and without an injected `agent_assignments` service (a stub/fake is
  sufficient — 7A does not need 7D merged to write or run these tests).
- `/capture category title | project name` — valid project, unknown project
  (clean rejection, no partial write), missing project (falls back to
  `project_id=None`, existing behavior unchanged).
- `/today` rendering includes owner + next action text.
- `/workitem <item_id>` — found, not found, sensitive-content-free error
  path.
- Regression: full existing `tests/test_control_tower_service.py` and
  `tests/test_telegram_control_tower.py` suites still pass unmodified except
  for 7A's own additive extensions.

## 11. Acceptance criteria

1. A `/capture` request with a project name creates a `WorkItem` linked to
   that `Project`, or fails cleanly if the project doesn't exist — no
   silent project creation.
2. `/today` and `/workitem <item_id>` both show status, owner, and next
   action for every listed item.
3. With no `AgentAssignment` service injected, owner/next-action still work
   correctly end to end (7D is not a hard dependency).
4. No schema change lands. `git diff` against `app/control_tower/schema.py`
   is empty.
5. Full existing regression suite passes unmodified.

## 12. Integration contract

Wave 1 (parallel with 7B, 7D — no code dependency at merge time). See
`docs/WAVE_6_SHARED_CONTRACTS.md` §5 for gates. 7A merges into the Wave 6
integration branch before 7C, since 7C's brief composition reads 7A's new
`owner_for()`/`next_action_for()` methods for full richness (7C can still
ship a v1 without them, per AD-W6-05, but the intended sequencing is 7A
before 7C).

## 13. Explicit prohibited edits

- No edits to `app/dispatch/**`, `app/nightshift/**`, `app/providers/**`,
  `app/dissertation/**`, `app/memory/**`, `app/agent_assignment/**`,
  `app/knowledge/**`, `app/brief/**`, `app/dashboard/**`.
- No new column on `control_tower_work_items` or any other existing table.
- No edits to another sprint's block in `app/main.py`/`app/telegram_bot.py`
  or another sprint's entry in `docs/CURRENT_SPRINT.md`.

## 14. Known risks / technical debt

- `owner_for()`'s fallback to `recommended_route` is a category label
  ("Development Agent"), not a specific agent instance — once 7D lands,
  the richer per-assignment owner supersedes it for items with an active
  assignment, but items without one still show the coarser label. This is
  intentional (graceful degradation), not a defect.
- The optional-constructor-injection interface between 7A and 7D is now
  frozen (`get_active_assignment_summary()`/`AssignmentSummary`, AD-W6-01) —
  the risk that the two sprints would need to negotiate a method signature
  post hoc is resolved by this freeze. The remaining, smaller risk is purely
  mechanical: `app/main.py`'s wiring of the injection can only happen once
  both 7A and 7D are merged, so 7A must still ship independently correct
  with the fallback-only path (§3, §11.3) until that wiring lands.
