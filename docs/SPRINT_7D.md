# Sprint 7D — Agent Registry & Assignment

## Status: **FROZEN** — final ChatGPT/Control Tower architecture decisions applied.

See `docs/WAVE_6_SHARED_CONTRACTS.md` §2.5, AD-W6-01, and AD-W6-06 for the
contract this spec implements.

**Frozen 7A ↔ 7D ownership (AD-W6-01, final):** 7D owns `AgentAssignment`
and all assignment state exclusively — no other Wave 6 sprint writes
`agent_assignments`. 7A (Control Tower) may read assignment data only
through the narrow, frozen `get_active_assignment_summary()` interface
defined in §3/§8 below; it never queries `agent_assignments` directly and
never stores what it reads.

## 1. Objective

Represent NOVA's execution capabilities explicitly — which agent is assigned
to which `WorkItem`, and (separately) which of NOVA's multi-model operators
that agent ultimately runs on — without building a fully autonomous
multi-agent system.

## 2. User-visible usable capability

A `WorkItem` can be explicitly assigned to a registered agent
(`AgentAssignment`), the assignment's status is visible via Telegram, and
once execution actually starts, the assignment links to the real
`DispatchRecord` doing the work.

## 3. Scope

- New `app/agent_assignment/` package: `models.py`, `schema.py`,
  `repository.py`, `service.py`, `operators.py`.
- `AgentAssignment` contract exactly as specified in
  `docs/WAVE_6_SHARED_CONTRACTS.md` §2.5.
- `AgentAssignmentService`:
  - `propose_assignment(work_item_id, requested_capability, agent_id, actor)
    -> AgentAssignment` — validates `agent_id`/`requested_capability`
    against the existing `AgentRegistry.validate_capability()` (read-only
    import from `app/dispatch/registry.py`; zero edits to that file).
  - `accept_assignment(assignment_id, actor) -> AgentAssignment`
  - `start_execution(assignment_id, actor) -> AgentAssignment` — the
    **only** method allowed to call
    `DispatchService.create_dispatch()`/`ApprovalService.request_approval()`,
    mirroring `NightShiftWorker`'s exclusive relationship with dispatch
    (Wave 3 §2). Never writes `dispatches`/`approvals` directly.
  - `complete_assignment(assignment_id, actor) -> AgentAssignment`,
    `cancel_assignment(assignment_id, actor, reason) -> AgentAssignment`,
    `reassign(assignment_id, new_agent_id, actor) -> AgentAssignment`
    (closes the old row, creates a new one — never mutates history).
  - `resolve_operator(agent_id: str) -> str` in `operators.py` — pure,
    derived function, no storage (AD-W6-06).
  - `get_active_assignment_summary(work_item_id: str) -> AssignmentSummary |
    None` — the **frozen read interface** 7A consumes (AD-W6-01). Returns
    the most recent non-terminal `AgentAssignment` for `work_item_id`, or
    `None` if there is none. `AssignmentSummary` (`app/agent_assignment/models.py`)
    is a separate, minimal, frozen dataclass — `assignment_id`,
    `assigned_agent_id`, `operator_id` (already resolved via
    `resolve_operator()`), `status` — never the full `AgentAssignment` row,
    so 7A can never accidentally read or propagate a field it doesn't own.
- Populate `docs/AGENT_REGISTRY.md`'s existing "Active agents" table with an
  additional documented column set for the Operator dimension (documentation
  only — the existing `AGENT_REGISTRY` dict in `app/dispatch/registry.py` is
  not modified; the doc gains a note, not a new agent).
- New read-only Telegram commands: `/assignments`,
  `/assignmentstatus <assignment_id>`.

## 4. Out of scope

- Agent spawning or dynamic agent registration.
- Agent-to-agent unrestricted messaging.
- Self-modification of any kind.
- Auto-commit, auto-merge, auto-push (Codex remains structurally blocked —
  no coding agent is ever registered with a `git_mutation` capability,
  Wave 3 §7, unchanged by this sprint).
- Autonomous recursive delegation (an `AgentAssignment` cannot create
  another `AgentAssignment`).
- Any new provider adapter, new `RegisteredAgent`, or new `Role` — this
  sprint reuses the existing three registries entirely (§5).

## 5. Existing architecture reused

- `app.dispatch.registry.AgentRegistry`/`RegisteredAgent` — the internal
  capability-category agent identity (`document_agent`, `coding_agent`,
  etc.). Read-only import; **no new agent is registered**.
- `app.dispatch.service.DispatchService`/`app.dispatch.approvals.ApprovalService`
  — the canonical execution/approval seam. `AgentAssignmentService` is a new
  *consumer* of these, exactly like `NightShiftWorker`, not a new dispatch
  system.
- `app.router.roles.Role`/`_REGISTRY` — the logical responsibility→provider
  mapping (`CONTROL_TOWER`→ChatGPT, `WORKSPACE_KNOWLEDGE`→Gemini,
  `TECHNICAL_ARCHITECT`→Claude, `EXECUTION_WORKER`→Codex, `FAST_ROUTER`).
  Read-only reference for `operators.py`'s derivation; not modified.
- `app.dispatch.adapters.ProviderGatewayAgentAdapter`'s existing internal
  routing table (`coding_agent`→`EXECUTION_WORKER`,
  `architecture_agent`→`TECHNICAL_ARCHITECT`, `generic_ai_agent`→
  `CONTROL_TOWER`) — the exact source of truth `resolve_operator()` mirrors;
  not modified, only read as a reference pattern (the mapping is
  re-expressed in `operators.py`, not imported as private internals, to
  avoid a fragile cross-package dependency on another package's local
  dict literal).
- `app.providers.registry.RegisteredModel`/`provider_id` — read-only
  reference confirming `Codex`/`Claude`/`9Router` are the real provider IDs
  behind the Operator labels.

## 6. Owned files/modules

- `app/agent_assignment/{models,schema,repository,service,operators}.py` —
  new, 7D-exclusive.
- `app/telegram_bot.py` — one additive block (two new read-only commands).
- `docs/AGENT_REGISTRY.md` — additive documentation note only (no table
  restructuring that would break another sprint reading it).
- `tests/test_agent_assignment_*.py` — new, 7D-exclusive.

## 7. Shared dependencies

- `app/dispatch/**` — read only: imports `AgentRegistry`, `DispatchService`,
  `ApprovalService` and calls their existing public methods. Zero edits.
- `app/router/roles.py` — read only.
- `app/providers/registry.py` — read only (reference/validation only, no
  provider network call originates here — that stays entirely inside
  `DispatchService`'s existing adapter seam).
- `app/control_tower/**` — 7D does **not** import Control Tower directly;
  the relationship is inverted (7A optionally injects
  `AgentAssignmentService` into `ControlTowerService`, not the reverse) to
  avoid a circular dependency, matching `ApprovalService`'s existing
  "ignorant of `DispatchService`, avoiding a circular import" design note
  (Wave 3 §2). The only surface 7A is permitted to call on the injected
  service is `get_active_assignment_summary()` (§3, §8) — frozen, final,
  not subject to per-merge negotiation.
- `app/main.py`, `app/telegram_bot.py` — shared, ordered append.

## 8. Data/contracts

New additive table, owned entirely by `app/agent_assignment/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS agent_assignments (
    assignment_id         TEXT PRIMARY KEY,
    work_item_id          TEXT NOT NULL,
    requested_capability  TEXT NOT NULL
                          CHECK (requested_capability IN
                              ('read_only','draft_only','external_communication','publication')),
    assigned_agent_id     TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'proposed'
                          CHECK (status IN
                              ('proposed','accepted','in_progress','completed','cancelled','reassigned')),
    dispatch_id           TEXT,
    requested_by          TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_assignments_work_item ON agent_assignments(work_item_id);
CREATE INDEX IF NOT EXISTS idx_agent_assignments_status ON agent_assignments(status);
CREATE TABLE IF NOT EXISTS agent_assignment_audit_log (
    id            INTEGER PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES agent_assignments(assignment_id) ON DELETE CASCADE,
    event         TEXT NOT NULL,
    actor         TEXT NOT NULL,
    from_status   TEXT,
    to_status     TEXT,
    detail        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
```

State machine (compare-and-swap, mirrors `DispatchService`'s transition
discipline exactly):

| From \ To | accepted | in_progress | completed | cancelled | reassigned |
|---|---|---|---|---|---|
| `proposed` | ✅ | – | – | ✅ | – |
| `accepted` | – | ✅ (`start_execution`) | – | ✅ | ✅ |
| `in_progress` | – | – | ✅ | – | ✅ |

Terminal: `completed`, `cancelled`, `reassigned`. `work_item_id` is a loose
reference (not a DB FK) — matches `DispatchRequest.source_id`'s discipline.

**Operator resolution** (`operators.py`, no table, pure function):

```python
_OPERATORS = {
    "CONTROL_TOWER": "Planning, reasoning, synthesis, acceptance (ChatGPT).",
    "CODEX":         "Implementation worker — builds and writes code/tests/docs.",
    "CLAUDE":        "Architecture, review, testing, defect correction.",
    "GEMINI":        "Google Workspace and knowledge operations.",
    "NINEROUTER":    "Background/general model execution.",
}

def resolve_operator(agent_id: str) -> str:
    agent = AgentRegistry().resolve_agent(agent_id)
    if agent.adapter_id == "local_deterministic":
        return "CONTROL_TOWER"
    if agent.adapter_id == "provider_gateway":
        return {
            "coding_agent": "CODEX",
            "architecture_agent": "CLAUDE",
            "generic_ai_agent": "NINEROUTER",
        }.get(agent_id, "NINEROUTER")
    return "CONTROL_TOWER"
```

`GEMINI` is reserved but currently unreachable from any registered
`agent_id` (documented gap, AD-W6-06, §7 of the shared contract).

**`AssignmentSummary`** (`app/agent_assignment/models.py`) — the frozen,
minimal read DTO returned by `get_active_assignment_summary()`. Not a
table; constructed on read from an `AgentAssignment` row:

```python
@dataclass(frozen=True)
class AssignmentSummary:
    assignment_id: str
    assigned_agent_id: str
    operator_id: str    # already resolved via resolve_operator()
    status: str
```

7A holds this value only for the duration of one `owner_for()`/
`next_action_for()` call (`docs/SPRINT_7A.md` §8) — it is never persisted,
cached, or written back by 7A. This is the entire read surface 7D exposes
to 7A; no other method on `AgentAssignmentService` is part of the frozen
7A-facing contract.

## 9. Security constraints

- `propose_assignment` fails closed on an unknown `agent_id` or unsupported
  `requested_capability` — no row is written, matching
  `DispatchService.create_dispatch()`'s existing discipline.
- Any assignment whose `requested_capability` is approval-required
  (`external_communication`, `publication`) must go through
  `ApprovalService.request_approval()` before `start_execution()` may call
  `DispatchService.dispatch()` — no bypass path.
- No new capability string is introduced — the closed vocabulary is reused
  verbatim from `AgentRegistry`.
- `reason`/`detail` free text screened with `SENSITIVE_CONTENT_PATTERN`.
- Telegram commands are read-only in this sprint (`/assignments`,
  `/assignmentstatus`) — no `/assign` write command ships from Telegram in
  v1; assignment creation is a service-level capability other Wave 6
  sprints (7A) or a future sprint can wire into Telegram once the
  interaction pattern (who initiates an assignment — the user, or Control
  Tower automatically on capture) is decided by Control Tower/ChatGPT. This
  is a deliberate scope cut, not an oversight — see §14.

## 10. Tests

- `propose_assignment`/`accept_assignment`/`start_execution`/
  `complete_assignment`/`cancel_assignment`/`reassign` — full transition
  matrix, including every forbidden transition raising the expected typed
  error.
- Unknown `agent_id`, unsupported `requested_capability` — fail closed,
  no row written.
- `start_execution` for an approval-required capability creates an
  `ApprovalRequest` and does not call `dispatch()` until approved (mirrors
  `NightShiftWorker.defer_for_approval()`'s test pattern).
- `resolve_operator()` — every current `agent_id` in `AGENT_REGISTRY`,
  confirming each resolves to one of the five documented operators (or
  `CONTROL_TOWER` as the local-deterministic default), and that no
  `agent_id` currently resolves to `GEMINI` (documents the known gap as a
  passing assertion, not a silent absence).
- A grep-based structural test (mirrors Wave 3 §8's precedent) asserting
  `app/agent_assignment/*.py` contains no raw SQL against
  `dispatches`/`dispatch_attempts`/`approvals`/`approval_audit`.

## 11. Acceptance criteria

1. An `AgentAssignment` can be proposed, accepted, started (creating a real
   linked `DispatchRecord`), and completed, entirely through
   `AgentAssignmentService` — no direct writes to `dispatches`/`approvals`.
2. `resolve_operator()` correctly labels every existing registered agent.
3. Zero edits to `app/dispatch/**`, `app/router/**`, `app/providers/**`.
4. Full existing regression suite passes unmodified.

## 12. Integration contract

Wave 1 (parallel with 7A, 7B — no code dependency on either at merge time).
7C (Wave 2) and 7A's optional injection (post-merge follow-up) both read
7D's public methods once merged.

## 13. Explicit prohibited edits

- No edits to `app/dispatch/**` (registry, service, approvals, adapters,
  schema) — read-only import only.
- No edits to `app/router/roles.py` or `app/providers/registry.py`.
- No edits to `app/control_tower/**` (the dependency is inverted — see §7).
- No edits to another sprint's block in `app/main.py`/`app/telegram_bot.py`.

## 14. Known risks / technical debt

- No Telegram write command creates an assignment in v1 (§9) — the
  interaction model (does the user propose assignments, or does Control
  Tower auto-propose one on `/capture`?) is a product decision for
  ChatGPT/Control Tower, not an architecture default this document should
  set unilaterally. This is a separate, still-open product question — it is
  not resolved by the 7A ↔ 7D ownership freeze (which settles *who owns
  what state and how it's read*, not *who triggers assignment creation*).
- `resolve_operator()` re-expresses (rather than imports)
  `ProviderGatewayAgentAdapter`'s internal routing table to avoid a fragile
  cross-package dependency on another package's private dict literal. This
  creates a small duplication risk: if `app/dispatch/adapters.py`'s routing
  table changes, `operators.py` must be updated in step. A structural test
  (§10) should assert the two stay in sync once both exist, to catch drift
  early rather than relying on manual coordination.
