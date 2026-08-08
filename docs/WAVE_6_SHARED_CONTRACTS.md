# Wave 6 Shared Contracts — NOVA Executive Office Foundation

## Status: **FROZEN** — final ChatGPT/Control Tower architecture decisions applied. Implementation not started.

**Naming resolution (final).** `docs/CURRENT_SPRINT.md` already uses "Wave 5"
(and 5.1–5.4) for Sprint 5G's provider-fallback hardening work, which is
merged. This initiative was drafted under the working label "Wave 5" and
has been renamed to **Wave 6** everywhere — this document, `docs/SPRINT_7A.md`
through `docs/SPRINT_7E.md`, and `docs/CURRENT_SPRINT.md`'s new entry — to
avoid colliding with the existing Sprint 5G-family log. This is a ChatGPT/
Control Tower decision applied in this pass, not an open question.

## Frozen decisions (this pass)

ChatGPT/Control Tower has reviewed the proposed contracts and applied the
following final decisions. Everything below is now binding on all five
Wave 6 worktrees; superseded language elsewhere in this document has been
updated to match.

1. **Wave numbering** — this initiative is Wave 6, not Wave 5, everywhere.
2. **Sprint 7E dashboard policy** — localhost only, read only, default OFF,
   explicit startup, no public bind, no new persistence, **no automatic
   server startup with the NOVA bot** (`app/main.py` is not touched by 7E
   at all — see AD-W6-07).
3. **7A ↔ 7D ownership** — 7A owns `WorkItem`/workflow/status/`next_action`;
   7D owns `AgentAssignment` and assignment state; 7A may consume assignment
   data only through a narrow, frozen read interface; `WorkItem` must never
   embed or duplicate `AgentAssignment` state; the owner/operator label 7A
   displays is always derived, never canonical assignment storage (see
   AD-W6-01, AD-W6-06, §4).

## Baseline

- Branch: `arch/w5-shared-contracts`. `main` includes Wave 5.4 (Sprint 5G.4,
  under review) and everything through Sprint 6B (dissertation evidence
  workflow, `308f3f6`).
- This document governs **five sprints implemented in parallel, in five
  separate worktrees**: 7A (Executive Workflow), 7B (Knowledge Operations),
  7C (Morning Executive Brief), 7D (Agent Registry & Assignment), 7E
  (Dashboard Skeleton).
- This is the third parallel-worktree contract in the repository's history.
  Wave 3 (`docs/WAVE_3_INTEGRATION_CONTRACT.md`) proved the pattern for two
  parallel sprints (5B.1, 5F): one canonical seam per concern, new work lands
  in new additive modules, and `app/main.py`/`app/telegram_bot.py` take one
  small ordered append per sprint. Wave 6 scales that same discipline to five
  sprints.

---

## 1. Existing architecture inventory

Grounding research read every relevant model/schema/service file and cross-
sprint doc before any contract below was proposed.

| Concern | Canonical module | State |
|---|---|---|
| Control Tower (WorkItem/Decision/Approval aggregation) | `app/control_tower/` | Merged (Sprint 5B), extended through 5G |
| Project/Task/Note/Session (Workspace Memory) | `app/memory/` | Merged (Sprint 2/2.1), frozen since |
| Dispatch (canonical agent-execution seam) | `app/dispatch/` | Merged (Sprint 5B.1), canonical per Wave 3 |
| Approval (canonical approval authority) | `app/dispatch/approvals.py` | Merged (Sprint 5B.1), canonical per Wave 3 |
| Night Shift (runtime modes, queue, automation) | `app/nightshift/` | Merged (5A.1, 5F automation) |
| Provider gateway / provider execution audit | `app/providers/` | Merged, hardened through 5G.1 |
| Execution (Sprint 3's low-level execution/risk record) | `app/execution/` | Merged, **not** the dispatch seam — separate identifier space by design (Wave 3 §3) |
| Dissertation workspace (Source/Evidence/Gap pattern) | `app/dissertation/` | Merged (6A, 6A.0, 6B) |
| Google Workspace (Calendar/Drive, read-only) | `app/google_workspace/` | Merged (5D, 5E), read-only |
| Role registry (logical responsibility → provider label) | `app/router/roles.py` | Merged, static |
| Provider/model registry | `app/providers/registry.py` | Merged, static |
| Agent registry (capability-category agents used by dispatch) | `app/dispatch/registry.py` | Merged (5B.1), static |
| Telegram surface | `app/telegram_bot.py` | Merged, additive command blocks per sprint |
| Audit | per-domain `*_audit_log` tables, one per domain, same shape | Established pattern, no central audit table |

**Key finding used throughout this document:** several concepts the brief
asked to "evaluate" already exist **twice**, deliberately, for documented
reasons — not by accident:

- **Decision** exists as both `app.memory.models.Decision` (legacy
  Workspace Memory decision register) and `app.control_tower.models.Decision`
  (formal register with `approved_by`/`effective_date`/`supersedes`). Sprint
  5B's own docs (`docs/executive-control-tower.md`) document this as
  intentional: `/decision Project | decision | rationale` writes the legacy
  register, `/decision summary` writes the Control Tower register.
- **MorningBrief** exists as both `app.nightshift.models.MorningBrief` (a
  persisted, immutable per-night-window snapshot) and
  `app.control_tower.models.MorningBrief` (a live, generated aggregation view
  that already embeds the night-shift snapshot). Neither is a mistake; they
  serve different scopes (§5 below).
- **Approval** has one canonical write authority (`ApprovalService`) and one
  read-only aggregation view (`ControlTowerService.list_approvals()`), by
  explicit design after Wave 3 found and consolidated three incompatible
  approval shapes into this one.

Wave 6 must not add a third/fourth version of anything on this list. The
contract evaluation below treats each of these as **REUSE — NO NEW CONTRACT
REQUIRED** unless there is a documented, compelling reason (AD-W6-01…10).

---

## 2. Contract evaluation

### 2.1 WorkItem — REUSE, extended behavior only (no schema change)

- **Exists:** yes. `app/control_tower/models.py:WorkItem`.
- **Canonical module:** `app/control_tower/` (models/repository/service/schema).
- **Owner:** Sprint 7A (inherits ownership of the whole `app/control_tower/`
  package for Wave 6; no other Wave 6 sprint edits it).
- **Identifier:** `item_id` (`TEXT`, `uuid4`).
- **Minimum fields:** `item_id`, `project_id` (nullable `int`, loose
  reference into `projects.id`, validated via `project_exists()` rather than
  a DB foreign key), `category` (closed `APPROVED_CATEGORIES` vocabulary),
  `title`, `summary`, `priority_score` (computed, stored), `urgency`,
  `importance`, `deadline` (UTC ISO-8601), `dependencies` (own join table,
  real FK, self-referencing, `ON DELETE RESTRICT`), `clarification_needs`,
  `recommended_route`, `status`, `created_at`, `updated_at`.
- **Status/state model:** `WORK_ITEM_STATES` = `inbox`,
  `clarification_needed`, `planned`, `in_progress`, `awaiting_approval`,
  `completed`, `deferred`, `cancelled`; `WORK_ITEM_TRANSITIONS` enforced in
  `ControlTowerService.transition_work_item()`; compare-and-swap persistence;
  `completed`/`cancelled` terminal.
- **Allowed relationships:** `project_id → memory.Project` (loose);
  `dependencies → WorkItem` (real FK); **new, additive:**
  `AgentAssignment.work_item_id → WorkItem.item_id` (loose string reference
  owned by 7D, same discipline as `DispatchRequest.source_id` — dispatch
  must not hard-depend on Control Tower's schema).
- **Validation boundaries:** category must be in `APPROVED_CATEGORIES`; text
  fields bounded and screened by `SENSITIVE_CONTENT_PATTERN`; deadline must
  be ISO-8601 (naive values interpreted as `Asia/Jakarta`); dependencies must
  exist and cannot self-reference.
- **Persistence:** SQLite, `control_tower_work_items` +
  `control_tower_work_dependencies`, additive `CREATE TABLE IF NOT EXISTS`.
- **Audit:** `control_tower_audit_log`, one row per state-changing operation,
  same transaction as the state write.
- **Read/write ownership:** writes only through
  `ControlTowerService`/`ControlTowerRepository`. No Wave 6 sprint writes
  `control_tower_work_items` directly.
- **Wave 6 schema changes required:** **none.** `owner` and `next_action`
  (both requested by 7A's objective) are **computed, not persisted** —
  see AD-W6-01.
- **Backward compatibility:** fully preserved; no column changes.

### 2.2 Project — REUSE, NO NEW CONTRACT REQUIRED

- **Exists:** yes. `app/memory/models.py:Project`, table `projects`
  (`app/memory/database.py`).
- **Owner:** Workspace Memory (`app/memory/`), frozen for Wave 6 — no sprint
  edits it.
- **Identifier:** `id` (`INTEGER` autoincrement).
- **Minimum fields:** `id`, `name` (unique, case-insensitive), `description`,
  `status` (`active`/`paused`/`completed`/`archived`), `created_at`,
  `updated_at`.
- **Relationships:** already the target of `WorkItem.project_id`
  (`ControlTowerRepository.project_exists()` already queries `projects`).
  Also has many `tasks`/`notes`/`decisions`/`sessions` (pre-existing,
  untouched).
- **Wave 6 schema changes required:** none. See AD-W6-02 for why no
  `ExecutiveProject` is introduced.

### 2.3 Decision — REUSE, NO NEW CONTRACT REQUIRED (both existing shapes kept)

- **Exists:** yes, twice, deliberately (see §1).
- **Wave 6 canonical:** `app.control_tower.models.Decision` — richer state
  (`approved_by`, `effective_date`, `status`, `supersedes`/`superseded_by`),
  already surfaced in `ControlTowerService.morning_brief()`. 7A/7C use this
  one via `ControlTowerService.register_decision()`.
- **Legacy:** `app.memory.models.Decision` is untouched; Wave 6 does not
  read or write it.
- **Wave 6 schema changes required:** none. See AD-W6-03.

### 2.4 Approval — REUSE, NO NEW CONTRACT REQUIRED

- **Exists:** yes. Canonical write authority: `ApprovalService`
  (`app/dispatch/approvals.py`), established by Wave 3 as "canonical
  approval authority for Wave 3 and later" after three incompatible shapes
  were found and consolidated. Read-only aggregation:
  `app.control_tower.models.Approval` via
  `ControlTowerService.list_approvals()`, which already folds in Control
  Tower links, `executions`, `night_queue_jobs`, and dispatch approvals.
- **Wave 6 requirement:** any Wave 6 sprint producing an approval-eligible
  action (7D's `AgentAssignment`) calls `ApprovalService.request_approval()`.
  No sprint invents a fifth approval shape. `list_approvals()` may be
  extended (read-only, additive) by 7D to fold in `agent_assignments`-sourced
  pending approvals the same way it already folds in the other four sources.
- **Wave 6 schema changes required:** none beyond 7D's own new table (§2.5).

### 2.5 AgentAssignment — NEW CONTRACT (minimal, layered, owned by 7D)

- **Exists:** no single contract by this name. Closest existing, overlapping
  concepts, all kept distinct rather than collapsed (AD-W6-06):
  `DispatchRecord` (source/agent/capability/status — the actual execution
  seam), `RegisteredAgent`/`AgentRegistry` (which internal capability-agent
  exists and what it can do), `Role` (logical responsibility → provider
  label), `RegisteredModel` (provider/model selection detail).
- **Canonical module (new):** `app/agent_assignment/` (models, schema,
  repository, service, `operators.py`).
- **Owner:** Sprint 7D.
- **Identifier:** `assignment_id` (`TEXT`, `uuid4`).
- **Minimum fields:** `assignment_id`, `work_item_id` (loose reference into
  `control_tower_work_items.item_id`, not a DB FK), `requested_capability`
  (reuses the existing closed vocabulary: `read_only`, `draft_only`,
  `external_communication`, `publication`), `assigned_agent_id` (reuses
  `AgentRegistry.agent_id` — no new agent identity is minted),
  `status`, `dispatch_id` (nullable, set once execution actually starts,
  references `dispatches.dispatch_id`), `requested_by`, `created_at`,
  `updated_at`.
- **Status/state model:** `proposed → accepted → in_progress → completed`;
  `proposed`/`accepted → cancelled`; `accepted`/`in_progress → reassigned`
  (closes the row, a new `AgentAssignment` is created — mirrors
  `retry_dispatch()`'s "new row, not a mutation" discipline). Terminal:
  `completed`, `cancelled`, `reassigned`.
- **Allowed relationships:** `work_item_id → WorkItem` (loose);
  `assigned_agent_id → AgentRegistry` (validated via the existing
  `validate_capability()`, read-only import, zero edits to
  `app/dispatch/registry.py`); `dispatch_id → DispatchRecord` (loose,
  set only after `DispatchService.create_dispatch()` succeeds).
- **Validation boundaries:** `assigned_agent_id`/`requested_capability`
  validated against the existing closed `AgentRegistry`; unknown agent or
  unsupported capability fails closed before any row is written (mirrors
  `DispatchService.create_dispatch()`).
- **Persistence:** SQLite, new additive table `agent_assignments`, owned
  entirely by `app/agent_assignment/schema.py` — zero edits to
  `app/dispatch/schema.py`.
- **Audit:** new `agent_assignment_audit_log`, same one-row-per-state-change-
  in-the-same-transaction discipline as every other domain.
- **Read/write ownership:** writes only through
  `AgentAssignmentService`. It is the **only** code allowed to call
  `DispatchService`/`ApprovalService` on behalf of an assignment (mirrors
  `NightShiftWorker`'s exclusive relationship with dispatch, Wave 3 §2) —
  it never writes `dispatches`/`approvals` directly.
- **Wave 6 schema changes required:** one new additive table, no changes to
  any existing table.
- **Backward compatibility:** fully additive; no other domain's schema
  changes.

### 2.6 KnowledgeItem — NEW CONTRACT (minimal, provenance-first, owned by 7B)

- **Exists:** no general-purpose contract. Closest existing precedent:
  `app.dissertation.models.Source`/`Evidence`/`ResearchNote` — a working,
  tested provenance pattern, but hard-scoped to the dissertation workspace's
  `chapter_id`/`gap_id` model.
- **Canonical module (new):** `app/knowledge/` (models, schema, repository,
  service). Distinct from the pre-existing, empty top-level `knowledge/`
  directory (Sprint 1.1 placeholder for governed knowledge *assets*, not
  code) — `app/knowledge/` is Python source, `knowledge/` stays a non-code
  asset/index location it may reference later.
- **Owner:** Sprint 7B.
- **Identifiers:** `KnowledgeSource.id`, `KnowledgeItem.id` (both `INTEGER`
  autoincrement, matching Dissertation's ID style since both are
  metadata-only local domains).
- **Minimum fields:**
  `KnowledgeSource`: `id`, `title`, `source_type` (`document`|`note`|
  `drive_file`|`calendar_event`|`manual`|`conversation`), `origin_system`
  (`manual`|`google_drive`|`google_calendar`|`dissertation`|`telegram`),
  `origin_ref` (opaque external reference, e.g. a Drive `file_id` — never raw
  content), `citation_text`, `status` (`active`|`archived`), `created_at`,
  `updated_at`.
  `KnowledgeItem`: `id`, `source_id`, `project_id` (nullable, loose link to
  `memory.Project`), `work_item_id` (nullable, loose link to `WorkItem`),
  `title`, `summary` (short, bounded finding — never raw document content),
  `tags`, `confidence` (`LOW`|`MEDIUM`|`HIGH`, mirrors Dissertation
  `Evidence.confidence` exactly), `created_at`, `updated_at`.
- **Status/state model:** `KnowledgeSource.status` only (`active`/
  `archived`); items themselves are immutable-once-created metadata records
  (Dissertation's append-only-evidence precedent), edits create a new item
  rather than mutating history.
- **Allowed relationships:** `KnowledgeItem.source_id → KnowledgeSource`
  (real FK, single-domain); `project_id`/`work_item_id` loose references
  (no FK, cross-domain — same discipline as `DispatchRequest.source_id`).
- **Validation boundaries:** all free text screened with
  `SENSITIVE_CONTENT_PATTERN` (reused, not reimplemented); `summary` and
  `citation_text` length-bounded; `confidence` closed enum.
- **Persistence:** SQLite, new additive tables `knowledge_sources`,
  `knowledge_items`.
- **Audit:** new `knowledge_audit_log`, same discipline.
- **Read/write ownership:** writes only through `KnowledgeService`, sourced
  from Telegram-driven manual entry in v1 (mirrors Dissertation's
  `/dissertation addsource`/`addevidence`) — no autonomous ingestion.
- **Wave 6 schema changes required:** two new additive tables, no changes to
  `app/dissertation/` or any other existing table.
- **Future Gemini boundary (not implemented this sprint):** `KnowledgeService`
  documents (but does not implement) a `register_source_from_drive(...)`
  seam accepting `app.google_workspace.drive.models.DriveFileMetadata` —
  read-only metadata only, matching Sprint 5D/5E's existing read-only
  posture. No crawling, no autonomous ingestion, no Drive/Calendar write
  capability is added.

### 2.7 BriefItem — REUSE pattern via new read-only composition (no new table)

- **Exists:** two shapes already, serving different scopes (§1): a
  persisted per-night-window snapshot (`app.nightshift.models.MorningBrief`,
  `morning_briefs` table) and a live generated aggregation
  (`app.control_tower.models.MorningBrief`, not persisted).
- **Wave 6 design:** `BriefItem` is **the DTO returned by a new, read-only
  composition service** — `app/brief/service.py`'s
  `ExecutiveBriefService.generate_morning_brief()` — not a new table. It
  calls existing public read methods only:
  `ControlTowerService.get_today_priorities()`,
  `ControlTowerService.list_approvals()`,
  `NightShiftService.get_latest_morning_brief()`, and (once landed)
  `AgentAssignmentService.list_active()`/`KnowledgeService`'s read surface.
  Zero edits to `app/control_tower/` or `app/nightshift/`.
- **Owner:** Sprint 7C, new `app/brief/` package (no `schema.py` — no
  persistence in v1).
- **No LLM dependency:** the composition is deterministic Python over
  already-canonical operational state, per the brief's explicit requirement.
  An LLM summarization layer is an optional, later, presentation-only
  addition and is out of scope here.
- **Wave 6 schema changes required:** none. See AD-W6-05 for the
  simplest-architecture rationale and the persisted-snapshot alternative
  considered.

### 2.8 ProviderExecution — REUSE, NO NEW CONTRACT REQUIRED

- **Exists:** yes, fully. `app.providers.models.ProviderAuditRecord` /
  `ProviderRequestAttempt`, table `provider_request_audit` +
  `provider_request_attempts` (`app/providers/schema.py`), hardened through
  Sprint 5G.1's three-identity distinction (NOVA alias / upstream route /
  resolved model label).
- **Owner:** `app/providers/`, frozen for Wave 6.
- **Wave 6 requirement:** 7D only *reads/correlates* — an `AgentAssignment`
  whose `dispatch_id` resolves to a `provider_gateway`-adapter dispatch is
  transitively linked to a `ProviderAuditRecord` via the dispatch layer's
  existing `payload_ref`/adapter mechanics
  (`app/dispatch/adapters.py:ProviderGatewayAgentAdapter`). No new provider
  audit fields, no new correlation table — `correlation_id` threading
  already established by prior sprints (e.g.
  `night_shift_job:<job_id>`) extends naturally to
  `control_tower_work_item:<item_id>:agent_assignment:<assignment_id>` if
  7D chooses to set one when calling `create_dispatch()`.
- **Wave 6 schema changes required:** none.

---

## 3. Architecture decisions

### AD-W6-01 Canonical WorkItem ownership — **FROZEN**

**Decision:** `app.control_tower.models.WorkItem` remains the sole canonical
WorkItem. 7A adds computed `owner_for(item)`/`next_action_for(item)` methods
to `ControlTowerService` — no schema change.

**Frozen 7A ↔ 7D ownership boundary (Control Tower directive, final):**

- **7A owns** `WorkItem`, its workflow, its `status`, and its `next_action`
  computation — exclusively. No other Wave 6 sprint reads or writes
  `control_tower_work_items` or any `WorkItem`-shaped state.
- **7D owns** `AgentAssignment` and all assignment state — exclusively. 7A
  never writes to `agent_assignments`, and `WorkItem` **must not embed or
  duplicate** any `AgentAssignment` field (no `assigned_agent_id`,
  `assignment_status`, or similar column is ever added to
  `control_tower_work_items` — this is the same rule as "no schema change"
  below, stated explicitly for the assignment relationship specifically).
- **7A consumes assignment data only through a narrow, frozen read
  interface** on the optionally-injected `AgentAssignmentService`:
  `get_active_assignment_summary(work_item_id: str) -> AssignmentSummary | None`,
  where `AssignmentSummary` is a small, read-only, non-persisted dataclass
  (`assignment_id`, `assigned_agent_id`, `operator_id`, `status`) owned and
  defined by 7D (`app/agent_assignment/models.py`). 7A holds this value only
  in memory for the duration of one `owner_for()`/`next_action_for()` call —
  it is never cached, stored, or written back anywhere by 7A. This
  supersedes the "exact method name differs from what 7A assumes" open item
  from the original proposal — the interface above is now the frozen
  contract both sprints implement against.
- **The owner/operator label 7A displays is always derived, never canonical
  assignment storage.** `owner_for()` returns `item.recommended_route`
  unless `AssignmentSummary.status` is non-terminal, in which case it
  returns `AssignmentSummary.assigned_agent_id`'s display name (and,
  optionally, `AssignmentSummary.operator_id` for the operator label per
  AD-W6-06). Nothing 7A displays is ever the source of truth — the source of
  truth for any assignment is always `agent_assignments`, owned by 7D.

**Alternative considered:** persist `owner_agent_id`/`next_action` as new
columns on `control_tower_work_items`. **Rejected:** (1) it forces a single
`ALTER TABLE` that every one of 7A/7D's parallel worktrees would need to
coordinate on, exactly the shared-file contention this wave exists to avoid;
(2) the value is fully derivable from existing status/dependency/assignment
data, matching the precedent already set by Dissertation's computed
`next_action` cascade and Workspace Memory's
`ContinueContext.recommended_next_action` — neither of which is stored.
Revisit only if a future sprint needs historical "what was the next action on
date X" queries.

### AD-W6-02 Canonical Project ownership

**Decision:** `app.memory.models.Project` (Workspace Memory) remains the sole
canonical Project. No `ExecutiveProject`.

**Alternative considered:** a Control-Tower-native Project scoped to
executive/business work, separate from Workspace Memory's Project.
**Rejected:** `WorkItem` already references `projects` via
`project_exists()`; Workspace Memory's `Project` (`name`/`description`/
`status` only) has no dev-specific fields — it is already domain-neutral.
Splitting it would immediately create the `ExecutiveProject` vs `Project`
duplication the brief explicitly forbids.

### AD-W6-03 Decision/approval reuse

**Decision:** both existing `Decision` shapes stay; Wave 6's canonical is
`control_tower.Decision`. `ApprovalService` remains the sole approval-write
authority; `control_tower.Approval` stays a read-only aggregation, extended
(read-only) by 7D to include `agent_assignments`-sourced pending approvals.

**Alternative considered:** unify the two `Decision` shapes into one.
**Rejected:** out of scope for Wave 6, already deliberately documented as
intentional dual-purpose in Sprint 5B's own docs, and unifying is a
migration-risk change with no Wave 6 functional requirement forcing it.

### AD-W6-04 KnowledgeItem boundary

**Decision:** new, minimal `app/knowledge/` package, provenance-first,
reusing Dissertation's Source/Evidence *pattern* (not its tables).

**Alternative considered:** generalize `app/dissertation/`'s models.
**Rejected:** its `Source`/`Evidence` are hard-scoped to
`chapter_id`/`gap_id` and its own workspace singleton; genericizing a
working, tested module for no Wave-6-required benefit risks regressing 6A/6B.
**Alternative considered:** put the new code in the top-level `knowledge/`
directory. **Rejected:** every other domain's real code lives under `app/`;
`knowledge/` (top-level) stays a non-code asset location per its original
Sprint 1.1 purpose.

### AD-W6-05 Brief generation model

**Decision:** `BriefItem` is a generated, read-only composition
(`app/brief/`), not a new persisted table. No LLM dependency.

**Alternative considered:** persist a new `brief_items` table
(Night-Shift-style immutable per-day snapshot). **Rejected** for v1: no
Wave 6 requirement needs brief history/replay, and a third persisted brief shape
would need its own additive schema plus a fourth aggregation entry point.
"Choose the simplest architecture" is the brief's own instruction; this is
that choice. Revisit if a future sprint needs brief history.

### AD-W6-06 Agent identity vs provider identity — **FROZEN**

**Decision:** four dimensions are kept separate: **Role**
(`app/router/roles.py`, responsibility abstraction), **RegisteredModel**
(`app/providers/registry.py`, provider/model selection),
**RegisteredAgent** (`app/dispatch/registry.py`, internal capability-category
agent used by dispatch/Telegram), and the new **Operator** identity
(`app/agent_assignment/operators.py` — `CONTROL_TOWER`/`CODEX`/`CLAUDE`/
`GEMINI`/`NINEROUTER`, the "who is accountable" label the brief asks for).
Operator is **derived, display-only** — computed from an existing
`agent_id`'s `adapter_id`, reusing the exact routing table already hardcoded
in `ProviderGatewayAgentAdapter.execute()`
(`coding_agent→EXECUTION_WORKER/Codex`,
`architecture_agent→TECHNICAL_ARCHITECT/Claude`,
`generic_ai_agent→CONTROL_TOWER/9Router`) — never a new stored identity.
`GEMINI` is reserved in the Operator registry but currently unreachable from
any registered `agent_id` (Google Workspace has no dispatch adapter yet) —
a documented, deliberate gap, not a defect.

**Alternative considered:** a single flat `agent_id` spanning all four
concerns. **Rejected:** this is precisely the conflation Sprint 5G.1 already
fixed once (NOVA alias vs upstream route vs resolved model), and the brief
explicitly requires keeping agent identity, provider model, role, and work
item distinct.

**Reaffirmed by the 7A ↔ 7D ownership freeze (AD-W6-01):** the Operator
label is computed by 7D's `resolve_operator()` and passed to 7A only inside
the frozen, ephemeral `AssignmentSummary` read DTO — it is never persisted
by 7A, never a `WorkItem` column, and never computed independently by 7A
from raw `agent_id` values (7A does not import `app/agent_assignment/operators.py`
directly; it only reads the `operator_id` field already resolved on the
`AssignmentSummary` it receives). This keeps operator derivation
single-sourced in 7D even though 7A displays the result.

### AD-W6-07 Dashboard read-only architecture — **FROZEN**

**Decision (final Sprint 7E dashboard policy):**

1. **Localhost only** — binds `127.0.0.1` exclusively; no configurable bind
   host in v1.
2. **Read only** — no write endpoint, no approve/reject/capture action, ever.
3. **Default OFF** — `NOVA_DASHBOARD_ENABLED` defaults to `false`.
4. **Explicit startup** — the dashboard is started by its own standalone
   entry point (e.g. `python -m app.dashboard.server`), invoked deliberately
   by an operator. It is not a mode of the bot process.
5. **No public bind** — same constraint as (1), stated separately because it
   governs both the bind address and the absence of any reverse proxy/TLS
   termination/port-forwarding guidance in this sprint.
6. **No new persistence** — `DashboardService` composes read-only calls to
   existing canonical services only; no new table, cache, or file store.
7. **No automatic server startup with the NOVA bot** — `app/main.py` is
   **not edited by 7E at all**. There is no conditional startup block, no
   background thread, and no code path by which running `python -m
   app.main` starts the dashboard under any settings combination. This
   supersedes the original proposal's "7E's process model is deferred to
   implementation" open item and its app/main.py-conditional-startup
   design — both are resolved by this freeze: **separate process, separate
   entry point, always.**

New `app/dashboard/` package (`server.py`, `views.py`, `service.py`) using
Python's stdlib `http.server`/`wsgiref` only (no new pip dependency),
server-rendered HTML populating the previously-empty `templates/`
directory. It composes read-only calls to `ControlTowerService`,
`ExecutiveBriefService`, `AgentAssignmentService`, `NightShiftService`,
`ProviderGatewayService`. `app/dashboard/server.py`'s own `__main__` reads
`NOVA_DASHBOARD_ENABLED`/`NOVA_DASHBOARD_PORT` (additive fields on the
existing `Settings`, `app/config.py`) as a defense-in-depth guard — refusing
to bind if the flag is unset — even though nothing else can invoke it.

**Alternative considered:** FastAPI/Flask + a JS frontend. **Rejected:** the
repo has zero HTTP/web dependencies today (`requirements.txt`/
`pyproject.toml` — Telegram polling only), and the brief explicitly says "No
complex frontend framework unless justified by existing repo." **Alternative
considered:** Telegram-only visibility. **Rejected** as not meeting the
"Executive Dashboard Skeleton" deliverable's distinct-surface intent, though
Telegram remains the primary write/interactive surface. **Alternative
considered (this freeze pass):** start the dashboard as a background thread
inside the existing bot process when `NOVA_DASHBOARD_ENABLED=true`, via a
conditional block in `app/main.py`. **Rejected by Control Tower directive:**
even gated behind a default-off flag, wiring dashboard startup into the
bot's own startup path means the dashboard's availability becomes a
function of how the bot happens to be launched, and it shares the bot's
crash/restart domain and `app/main.py` (a shared, ordered-append file every
other Wave 6 sprint also touches) for a surface with no write capability
and no other Wave 6 dependency on the bot process. A fully separate,
explicitly-invoked entry point removes both risks at zero cost, since
nothing about the dashboard's read-only composition requires being in the
same process as Telegram polling.

### AD-W6-08 Parallel worktree ownership

**Decision:** see the Ownership Matrix (§4). Each of 7A/7B/7D gets a wholly
new, additive package (or, for 7A, exclusive ownership of pre-existing
`app/control_tower/`) with zero file-level overlap. 7C and 7E only *import
and call* (never edit) the packages they depend on — the same pattern
`NightShiftWorker` already uses against `DispatchService`/`ApprovalService`.
The only genuinely shared files are `app/main.py`, `app/telegram_bot.py`, and
`docs/CURRENT_SPRINT.md`, governed by the append-one-ordered-block discipline
Wave 3 already proved across two real parallel sprints.

### AD-W6-09 Integration sequencing

**Decision:** Wave 1 (parallel, no inter-dependency): 7A, 7B, 7D. Wave 2:
7C (reads 7A's/7D's landed public methods). Wave 3: 7E (reads all four).

**Alternative considered:** strict fully-sequential 7A→7B→7C→7D→7E.
**Rejected:** unnecessarily serializes work with no real data dependency
between 7A/7B/7D. **Alternative considered:** all five fully parallel
including 7E. **Rejected:** 7E's spec requires it to "consume canonical
domain services," which for 7C/7D-sourced data don't exist until those merge.

### AD-W6-10 Human approval boundaries

**Decision:** no new approval mechanism. Every Wave 6 sprint that can
produce an approval-eligible action routes through the existing
`ApprovalService`/dispatch approval-policy table (Wave 3 §6), unchanged.
Safe-autonomous vocabulary (READ/ANALYZE/DRAFT/QUEUE/RECOMMEND/TEST) maps
onto the existing `read_only`/`draft_only` capability tier; approval-gated
vocabulary (SEND/PUBLISH/DELETE/PURCHASE/COMMIT/PUSH/MERGE/DESTRUCTIVE
UPDATE/EXTERNAL SIDE EFFECT) maps onto existing `external_communication`/
`publication` (approval-required) and the permanently-prohibited set (git
mutation, secret change, destructive ops). No Wave 6 sprint registers a
capability outside this closed vocabulary; Codex remains structurally
blocked from commit/push/merge by the existing rule that no coding agent is
ever registered with a `git_mutation` capability (Wave 3 §7).

---

## 4. Ownership matrix

| Module / Contract | Shared/Core | 7A | 7B | 7C | 7D | 7E |
|---|---|---|---|---|---|---|
| `app/control_tower/**` | — | **OWNER** | READ ONLY | READ ONLY | READ ONLY | READ ONLY |
| `app/memory/**` (Project/Task/Note/Session) | SHARED/CORE, frozen | READ ONLY | READ ONLY | READ ONLY | — | READ ONLY |
| `app/dispatch/**` (Dispatch/Approval/AgentRegistry) | SHARED/CORE, frozen | — | — | — | READ ONLY (import only) | READ ONLY (import only) |
| `app/providers/**` | SHARED/CORE, frozen | — | — | — | READ ONLY (indirect) | READ ONLY (indirect) |
| `app/nightshift/**` | SHARED/CORE, frozen | — | — | READ ONLY | — | READ ONLY |
| `app/dissertation/**` | SHARED/CORE, frozen, no Wave 6 relation | FORBIDDEN | FORBIDDEN | FORBIDDEN | FORBIDDEN | FORBIDDEN |
| `app/google_workspace/**` | SHARED/CORE, frozen | — | READ ONLY (types only) | — | — | READ ONLY (types only) |
| `app/router/roles.py`, `app/execution/**`, `app/security.py` | SHARED/CORE, frozen | — | — | — | READ ONLY (`roles.py`) | — |
| `app/knowledge/**` (new) | — | — | **OWNER** | READ ONLY (optional) | — | READ ONLY |
| `app/agent_assignment/**` (new) | — | SHARED DEPENDENCY (optional DI, no edits) | — | READ ONLY | **OWNER** | READ ONLY |
| `app/brief/**` (new) | — | — | — | **OWNER** | — | READ ONLY |
| `app/dashboard/**`, `templates/**` (new) | — | — | — | — | — | **OWNER** |
| `app/config.py`, `.env.example` | SHARED, ORDERED | — | — | — | — | append block (new settings fields only) |
| `app/main.py` | SHARED, ORDERED | append block | append block | append block | append block | **not touched (frozen, AD-W6-07)** |
| `app/telegram_bot.py` | SHARED, ORDERED | append block | append block | append block (new cmd) | append block | append block (optional, status-only cmd — never starts the server) |
| `docs/CURRENT_SPRINT.md` | SHARED, SEQUENTIAL | own entry | own entry | own entry | own entry | own entry |
| `docs/AGENT_REGISTRY.md` | READ ONLY (pre-populated, 5B.1) | — | — | — | READ ONLY | — |
| `tests/**` | each sprint owns only its new files | new files | new files | new files | new files | new files |

**Files that MUST NOT be independently edited by more than one worktree:**
`app/control_tower/{models,service,repository,schema}.py` (7A exclusive —
also the exclusive owner of `WorkItem`/workflow/status/`next_action` per the
AD-W6-01 freeze); `app/agent_assignment/{models,service,repository,schema}.py`
(7D exclusive — also the exclusive owner of `AgentAssignment`/assignment
state per the AD-W6-01 freeze; 7A never writes here, only reads the frozen
`AssignmentSummary` DTO); every file under `app/dispatch/`, `app/nightshift/`,
`app/providers/`, `app/dissertation/`, `app/memory/` (frozen — no Wave 6
sprint edits any of these at all); `app/main.py` (shared, strictly
append-only, one sprint's block per merge, never another sprint's block —
**and never touched by 7E at all**, per the AD-W6-07 freeze); `app/telegram_bot.py`
(same append-only discipline, all five sprints).

---

## 5. Integration contract

### Order

```
Shared contracts (this document)
        ↓
   ┌────┼────┐
   7A   7B   7D      (parallel — no inter-dependency at merge time)
   └────┼────┘
        ↓
        7C            (reads 7A's/7D's landed public methods)
        ↓
        7E            (reads 7A/7B/7C/7D's landed public methods)
```

Derived from the actual read/write dependencies established in §2, not
assumed: 7A's owner/next-action computation degrades gracefully to
`recommended_route` with no `AgentAssignment` present, so it does not block
on 7D. 7B has no dependency on any other Wave 6 sprint. 7C's brief
composition calls 7A's and 7D's public methods and is materially richer once
they exist. 7E's spec requires it to consume canonical domain services,
several of which (7C's brief, 7D's assignments) do not exist until merged.

### Gates (per sprint, before merge to the Wave 6 integration branch)

1. Mini-sprint targeted tests (new package's own test files) pass.
2. Claude review/fix pass on the diff.
3. Mini-sprint acceptance matrix (defined in each `docs/SPRINT_7*.md`)
   satisfied.
4. Merge to a Wave 6 integration branch (not `main`) in the order above.
5. Full canonical regression suite (`pytest`, currently 644+ passing) run
   against the integration branch after each merge.
6. Security checks: no secret/credential logging, `SENSITIVE_CONTENT_PATTERN`
   applied to all new free-text fields, no new capability outside the closed
   vocabulary (AD-W6-10).
7. Final ChatGPT/Control Tower acceptance review.
8. Human approval before commit/merge/push into `main`. No sprint commits,
   merges, or pushes on its own authority — matching the operating model's
   "Codex must stop before commit/push" rule.

No automatic integration at any gate.

---

## 6. Security / approval contract

Preserves the existing NOVA approval architecture exactly (AD-W6-10) — no
parallel mechanism.

**Safe autonomous actions (no approval required):** READ, ANALYZE, DRAFT,
QUEUE, RECOMMEND, TEST — maps onto the existing `read_only`/`draft_only`
dispatch capability tier.

**Approval-gated actions:** SEND, PUBLISH, DELETE, PURCHASE, COMMIT, PUSH,
MERGE, DESTRUCTIVE UPDATE, EXTERNAL SIDE EFFECT — maps onto
`external_communication`/`publication` (approval-required via
`ApprovalService`) and the permanently-prohibited set (git mutation, secret
change, destructive file/DB operations — never approval-eligible at all).

No Wave 6 sprint introduces a new agent capability string, a new approval
table, or a new authorization check outside `app.security.SENSITIVE_CONTENT_PATTERN`
and the existing single-authorized-Telegram-user model.

---

## 7. Resolved by this freeze / remaining open questions

**Resolved (this pass):**

- Wave numbering — this is Wave 6, everywhere (§ top of document).
- Sprint 7E dashboard policy — frozen per AD-W6-07's seven-point policy;
  no longer deferred to implementation.
- 7A ↔ 7D ownership boundary and the `AssignmentSummary` read interface —
  frozen per AD-W6-01; no longer an assumed/negotiable interface.

**Still open for ChatGPT/Control Tower:**

1. **`GEMINI` operator reachability** (AD-W6-06) — Google Workspace has no
   dispatch adapter yet, so no `AgentAssignment` can currently resolve to
   `GEMINI`. This remains an acceptable Wave 6 gap, not a defect, unless
   Control Tower wants to bring a Workspace-routed dispatch adapter into
   scope (it is explicitly out of scope per the original brief and is not
   changed by this freeze).
