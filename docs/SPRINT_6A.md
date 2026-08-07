# Sprint 6A — Full Dissertation Workspace

## Status: Proposed (architecture, not yet implemented)

## Baseline

- `main` = current tip, Wave 3 fully merged (`e99215b` — JobQueue dependency
  fix is the latest commit; Sprint 5B.1 merged at `8c6f64a`, Sprint 5F merged
  at `749fa21`).
- Full regression: **492 tests passed**. Must not regress.
- Sprint 6A.0 (`app/dissertation/`) is merged and is the foundation this
  sprint extends — see §1.

---

## 1. Existing Foundation Assessment

### A. What 6A.0 already implemented (verified by reading the actual code,
not the original `SPRINT_6A0.md` draft — this codebase has one prior
precedent, Sprint 5A.1, where the shipped implementation diverged from its
own draft doc; 6A.0 did **not** diverge, but it is still read from source)

`app/dissertation/` (663 lines: `models.py`, `schema.py`, `repository.py`,
`service.py`) plus `docs/dissertation-workspace.md` and three test files
(`test_dissertation_schema.py`, `test_dissertation_repository.py`,
`test_dissertation_service.py`).

Six tables, all live in `app/dissertation/schema.py`'s `SCHEMA` string,
applied via `DissertationService.initialize()`:

- `dissertation_chapters(id, title, order_index, status, created_at, updated_at)`
  — `status ∈ {draft, in_review, revised, final}`, no transition matrix
  enforced (any status → any status is currently legal via
  `update_chapter_status`).
- `dissertation_subchapters` — identical shape, `chapter_id` FK `ON DELETE CASCADE`.
- `dissertation_document_versions(id, target_type, target_id, content_hash,
  source, version_state, created_at)` — `target_type ∈ {chapter,
  subchapter}`, append-only, **never stores raw content**, only a validated
  SHA-256 hex digest. `version_state` was added by a guarded
  `ALTER TABLE ... ADD COLUMN` (`VERSION_STATE_MIGRATION`, applied only if
  the column is missing per `PRAGMA table_info`) with its own strict
  transition matrix `original → working → reviewed → approved` (terminal).
- `dissertation_paragraph_maps(id, version_id, paragraph_ordinal,
  stable_paragraph_id, created_at)` — `UNIQUE(version_id,
  paragraph_ordinal)`; `stable_paragraph_id` is a deterministic `uuid5`
  derived from `(version_id, ordinal)`, so rebuilding a map is idempotent.
- `dissertation_review_jobs(id, target_type, target_id, status, summary,
  created_at, updated_at)` — `queued → in_progress → {completed, failed}`,
  CAS-guarded (`WHERE id = ? AND status = ?`, rejects concurrent-change with
  `InvalidReviewJobTransitionError`). **This is a lifecycle placeholder
  only — nothing anywhere in the codebase ever creates or executes a real
  review; it exists so a future sprint has a place to record "a review of X
  happened."**
- `dissertation_revision_log(id, target_type, target_id, actor, reason,
  created_at)` — append-only, `reason` passed through
  `SENSITIVE_CONTENT_PATTERN`, scoped only to `target_type ∈ {chapter,
  subchapter}` (baked into the table's own `CHECK`, not alterable in place).
- Two `BEFORE DELETE` triggers cascade version cleanup from chapter/subchapter
  deletion (documents versions aren't declared with `ON DELETE CASCADE`
  directly since `target_type`/`target_id` is a polymorphic pseudo-FK, not a
  real one).

`DissertationService` (the only entry point — no direct repository access
from outside `app/dissertation/`) exposes: `create_chapter`, `list_chapters`,
`update_chapter_status`, `create_subchapter`, `list_subchapters`,
`update_subchapter_status`, `record_document_version`,
`update_document_version_state`, `build_paragraph_map`, `create_review_job`,
`update_review_job_status`, `append_revision_log`. All validated
(`InvalidDissertationValueError` on bad input, `SENSITIVE_CONTENT_PATTERN`
guard on every free-text field: `reason`, `summary`).

**Confirmed zero footprint outside `app/dissertation/`**: no Telegram
command references `dissertation` anywhere in `app/telegram_bot.py` (grep
confirmed); `app/main.py` constructs and initializes `DissertationService`
but never passes it into `build_application()` — it is currently
**inert from a user's perspective**, exactly as `docs/dissertation-workspace.md`
states ("Sprint 6A.1 can add a Telegram interface"). No dependency on
`app.google`, `app.providers`, `app.execution`, `app.router`, or
`app.control_tower` exists in `app/dissertation/` today.

### B. What can be extended

- `dissertation_chapters` — additively, for `current_focus` (two new
  columns; §3).
- `app/dissertation/schema.py`'s `apply_schema()` — already has a proven
  guarded-column-addition pattern (`VERSION_STATE_MIGRATION`); Sprint 6A
  reuses that exact pattern for its own new columns.
- `DissertationService`/`DissertationRepository` — new methods appended,
  same validation/error conventions, same `_many()` helper style.
- `docs/dissertation-workspace.md` — extended, not replaced.

### C. What must not be duplicated

- **Tasks.** NOVA already has two task-shaped systems: Workspace Memory's
  flat `Task` (`app/memory/models.py` — `project_id, title, description,
  status, priority, due_date`, no category, no dependency graph, no
  approval awareness) and Control Tower's `WorkItem`
  (`app/control_tower/models.py` — `item_id, project_id, category, title,
  summary, priority_score (computed), urgency, importance, deadline,
  dependencies: list[str], clarification_needs, recommended_route, status ∈
  {inbox, clarification_needed, planned, in_progress, awaiting_approval,
  completed, deferred, cancelled}`). Control Tower's `APPROVED_CATEGORIES`
  **already includes `"academic"`**, and `ROUTES["academic"] = "Academic
  Agent"` — Wave 3's `AgentRegistry` already registers `academic_agent`
  with `{read_only, draft_only}` capabilities. A third, dissertation-owned
  task table would be the exact "duplicated task system" §18 warns against,
  and would ignore infrastructure Wave 3 already built specifically for
  this category.
- **Decisions.** Two exist already: Workspace Memory's flat `Decision`
  (`project_id, decision, reason`) and Control Tower's `Decision`
  (`decision_id, project_id, summary, rationale, impact, approved_by,
  effective_date, status, supersedes, superseded_by`) — the latter is
  materially the right shape for "methodology decision," "scope decision"
  (has rationale, impact, approver, and a supersession chain for when a
  decision is later revised). `ControlTowerService.register_decision()`
  already accepts `project_id: int | None = None`, so it does not require
  the dissertation to be modeled as a Workspace Memory project.
- **Audit.** `dissertation_revision_log`'s `target_type` `CHECK` is fixed at
  table-creation time to `('chapter', 'subchapter')` and SQLite cannot
  widen a `CHECK` constraint via `ALTER TABLE`. Sprint 6A does **not**
  attempt a destructive table-rebuild migration to widen it (forbidden by
  §9/§10 of the brief). New entities get their own audit table (§3),
  mirroring the established repo-wide pattern where every domain owns its
  own audit log rather than sharing one (`execution_audit_log`,
  `night_shift_audit_log`, `control_tower_audit_log`, `dispatch_audit_log`,
  `approval_audit` are all separate).
- **Dispatch/Approval.** Wave 3's `DispatchService`/`ApprovalService`
  (`app/dispatch/`) are canonical. Sprint 6A takes **no dependency on them
  at all** (§7, §8) — see AD-12 for the seam a future sprint uses instead
  of Sprint 6A building one prematurely.
- **Memory/Notes.** Workspace Memory's `Note` (`project_id, content`) has no
  chapter/source/evidence/gap linkage and is a flat per-project scratchpad.
  Retrofitting academic relationships onto it would push domain-specific
  structure into a general-purpose system — the wrong direction of
  "duplication." See AD-5/§4.5 for the explicit boundary.

### D. Authoritative today

`app/dissertation/schema.py` (not any draft SQL in a doc), `models.py`,
`repository.py`, `service.py` are the single source of truth for 6A.0's six
tables. `app/control_tower/` is the single source of truth for categorized
work items and rationale-bearing decisions. `app/memory/` remains the single
source of truth for general personal/operational tasks, notes, and
lightweight decisions unrelated to a structured academic domain.

### Technical debt relevant to 6A

- `dissertation_chapters`/`dissertation_subchapters` have **no `CHECK`-based
  transition matrix** for `status` (unlike `dissertation_document_versions`
  and `dissertation_review_jobs`, which are both CAS/transition-guarded).
  Sprint 6A does not fix this retroactively (out of scope — `update_chapter_status`'s
  behavior is unchanged) but the new progress model (§4.9) is deliberately
  designed to tolerate any status value reachable today.
- No `dissertation_workspace` root record exists at all — see AD-2.

---

## 2. Architecture Decisions

**AD-1 — What existing 6A.0 components become authoritative?**
All of them, unchanged in behavior: `Chapter`, `Subchapter`,
`DocumentVersion`, `ParagraphMap`, `ReviewJob`, `RevisionLogEntry`,
`DissertationService`, `DissertationRepository`, and the guarded-migration
pattern in `schema.py`. Nothing from 6A.0 is deprecated, renamed, or
replaced. `Chapter` gains two additive columns (AD-3); everything else is
extended by addition only.

**AD-2 — What is the canonical dissertation aggregate/root?**
A new **singleton** table, `dissertation_workspace`, mirroring the existing
`runtime_mode_state` singleton pattern from `app/nightshift/schema.py`
(`id INTEGER PRIMARY KEY CHECK (id = 1)`) — NOVA manages exactly one
dissertation for the one authorized user, consistent with every other
single-user assumption already baked into this codebase (one Telegram
owner, one approval authority). It holds identity (`title`, `program`),
`status`, a manually-settable `current_focus`, and timestamps. **It does
not store a progress number or a stored `next_action`** — see AD-8/AD-9.

**AD-3 — How are chapters represented?**
Reuse `dissertation_chapters` as-is (id, title, order_index, status,
created_at, updated_at), plus one additive column: `current_focus TEXT NOT
NULL DEFAULT ''` (manually settable, mirrors the workspace-level field).
**No `next_action` column is added to the table** — chapter-level next
action is always computed, never stored (AD-9). Subchapters are **not**
extended with `current_focus` in Sprint 6A — every §5 use case and §4.2
requirement operates at chapter granularity ("Bab II sudah sampai mana?"),
and extending subchapters too is exactly the kind of "nice to have" scope
expansion §14 warns against. This is a deliberate, documented boundary, not
an oversight.

**AD-4 — How are research sources represented?**
A new table, `dissertation_sources`, as **metadata + linkage
infrastructure only** — no full-text storage, no crawling, no citation
formatting engine (§14). Fields: `id`, `title`, `source_type` (closed
`CHECK` enum), `citation_text` (a single freeform formatted-citation string
the user supplies — NOT computed by NOVA), `locator` (nullable — URL, DOI,
ISBN, or a Drive file ID; one generic bounded text field, not five
type-specific columns), `status` (`unread | reviewed | cited | rejected`),
timestamps. Chapter relationship is many-to-many (`dissertation_source_chapter_links`)
because one source legitimately supports multiple chapters (literature
review sources especially) and one chapter cites many sources.

**AD-5 — How is evidence different from source and note?**
- **Source** = bibliographic metadata: *where information came from*.
- **Evidence** = one specific finding/claim/statistic *extracted from
  exactly one source*, with light provenance (`source_id NOT NULL`,
  optional `chapter_id`, optional `gap_id`). `summary` is the researcher's
  own paraphrase/short-quote, length-bounded (1,000 chars) specifically so
  this table cannot become a backdoor for storing full copyrighted text
  (§9 constraint 9/10). Evidence with no source is structurally
  impossible — `source_id` is `NOT NULL` at the schema level, and
  the service layer verifies the source exists before insert, directly
  satisfying Scenario C ("no orphan evidence should silently appear as
  academically authoritative").
- **Research Note** = a structured academic working-knowledge record that
  is deliberately allowed to be looser than evidence (no required source) —
  it captures analysis, synthesis, supervisor feedback, or an open question,
  and must relate to at least one dissertation object (`chapter_id`,
  `source_id`, `evidence_id`, or `gap_id` — `CHECK` requires at least one
  non-null). The **general-vs-dissertation boundary**: Workspace Memory's
  `Note` is an unstructured per-project scratchpad with zero academic
  relationships and is left completely alone; `dissertation_notes` exists
  specifically because it requires exactly the relational structure
  Workspace Memory's `Note` does not have and should not be forced to grow.

**AD-6 — Does research task reuse or reference existing NOVA task infrastructure?**
**Reference Control Tower's `WorkItem`, category `"academic"`.** Not
Workspace Memory's `Task` (wrong semantic fit — no category, no priority
scoring, no approval awareness, no dependency graph) and not a new
dissertation-specific task table (would duplicate exactly the
category-routed, approval-aware work-item system Wave 3 already built and
already wired `"academic"` into). `DissertationService` gains an optional
constructor collaborator, `control_tower: ControlTowerService | None =
None` (mirroring the exact optional-collaborator pattern
`ControlTowerService` itself already uses for `execution`/`night_shift`/
`approvals`). `create_research_task(...)` calls
`self.control_tower.capture_work(category="academic", ...)`, receives back
a `WorkItem`, and persists only a **local linkage row**
(`dissertation_research_task_links`: `chapter_id` nullable, `gap_id`
nullable, `work_item_id` — the Control Tower `item_id` string) — the task's
own title/status/priority/dependencies remain owned and mutated exclusively
by `ControlTowerService`. Reading "pending tasks for Chapter II" means:
read the local links for that chapter, then resolve each `work_item_id`
through `ControlTowerService`'s existing `get_work_item`-equivalent lookup
(via `self.control_tower.repository.get_work_item(...)`, the same accessor
Control Tower already uses internally).

**AD-7 — Does dissertation decision reuse/reference existing NOVA decision infrastructure?**
Same pattern as AD-6: **reference Control Tower's `Decision` registry.**
`create_decision_link(...)` calls `self.control_tower.register_decision(...)`
and persists a local `dissertation_decision_links` row (`chapter_id`
nullable, `decision_id` — the Control Tower `decision_id` string). No new
decision table with its own `summary`/`rationale`/`approved_by` fields is
created — that would duplicate a system that already has supersession
tracking, which a dissertation "methodology decision, later revised" case
needs.

**AD-8 — How is progress calculated?**
**Deterministic, computed on demand, never stored** (avoids the "false
precision" and "missing migration coverage for a stale cached number" both
called out in §18). Fixed weight table:
`{draft: 0, in_review: 50, revised: 75, final: 100}`.
- *Chapter progress* = average of subchapters' weights if the chapter has
  subchapters, else the chapter's own weight.
- *Dissertation overall progress* = unweighted mean of all chapters'
  progress. Zero chapters → progress is `0` and the overview says so
  explicitly rather than dividing by zero or omitting the field.
No chapter-importance weighting, no task/gap-count blending into the single
percentage — open-gap and pending-task **counts** are surfaced *alongside*
the percentage as separate, non-numeric signals (§4.9's explicit
instruction against false precision), never folded into it.

**AD-9 — How is next action determined?**
A deterministic rule cascade, zero LLM calls, implemented as one pure
function over already-loaded domain state (§7 — "do not introduce a model
call simply to calculate status"). Exact order (first match wins, chapters
walked in `order_index` ascending within a scope of "all chapters" or one
chapter if `chapter_id` is given):

```text
1. Any open gap with priority == 'critical' in scope
       → "Resolve critical gap: <description> (Chapter <n>)"
2. Any pending linked research task in scope
   (Control Tower status ∈ {inbox, clarification_needed, planned,
    in_progress, awaiting_approval})
       → "Continue task: <work item title> (Chapter <n>)"
3. Any open gap (any priority) in scope
       → "Address open gap: <description> (Chapter <n>)"
4. Any chapter in scope with status not in {final} and no pending
   task/gap found above
       → "Define next research task for Chapter <n> (<title>)"
5. All chapters in scope are 'final'
       → "All chapters are final. Consider a full-dissertation review pass."
6. No chapters exist in scope
       → "No chapters defined yet. Create Chapter I to begin."
```

**AD-10 — What is the minimal Telegram/control surface?**
One command family, `/dissertation [subview] [args]`, following the
**existing** `/nightqueue <subaction> ...` precedent from Sprint 5F (not a
new convention) — not eight separate top-level commands. Subviews:
`(none)` = overview, `chapter <n>`, `gaps`, `next`, `tasks`, `evidence <n>`,
`sources [n]`, `decisions`. Every one of the seven required Indonesian
questions (§5) maps to exactly one subview (see §4 of this document, or the
worked mapping in the file-level plan §5 below). **Write commands
(capturing a new source/gap/evidence/note) are explicitly deferred** — see
"Out of scope" — because every §5 required question is a read, and rushing
academic-content-entry UX into single-line slash commands is exactly the
kind of scope pressure §18 warns against. The service layer's `create_*`
methods exist and are fully tested this sprint regardless, so nothing about
a follow-up write-surface sprint is blocked.

**AD-11 — How does Google Workspace remain optional?**
Trivially: `DissertationService` takes **no dependency on
`app.google_workspace` at all**, in either direction. `dissertation_sources.locator`
is a plain nullable text field that *may* happen to hold a Drive file ID or
URL, but nothing in `app/dissertation/` ever calls a Google API to read or
validate it. A future sprint that wants "open this source in Drive" reads
`locator` and hands it to the *existing* read-only Drive service — entirely
outside this sprint's dependency graph.

**AD-12 — What extension seam is provided for future Sprint 6B without implementing 6B now?**
Two seams, both structural, neither implemented:
1. `DissertationService.create_research_task(...)`/`create_decision_link(...)`
   accept plain `actor: str` + structured fields — no Telegram-specific
   coupling — so a future Night Shift/Dispatch-driven caller (not just a
   Telegram handler) can call them identically.
2. A future 6B sprint that wants "dispatch a research task to Night Shift"
   would add a new `job_type` to `app/nightshift/classifier.py`'s
   `REGISTERED_JOBS` (e.g. `dissertation_evidence_gather`) and a
   `DispatchResult → dissertation_evidence`/`dissertation_notes` recording
   method — Sprint 6A does not add that classifier entry, does not touch
   `app/dispatch/` or `app/nightshift/` at all, and does not build a
   result-recording method with no caller. The seam is "the write methods
   already accept the right shape of input," not "a half-built pipeline."

---

## 3. Proposed Domain Model

All tables additive (`CREATE TABLE IF NOT EXISTS`), same file
(`app/dissertation/schema.py`), same `apply_schema()` entry point. Two
additive columns on an existing table, applied with the exact
`PRAGMA table_info(...)`-guarded pattern `VERSION_STATE_MIGRATION` already
established in the same file — never a blind `try/except`.

### 3.1 `dissertation_workspace` (new, singleton)

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY CHECK (id = 1)` | Singleton, mirrors `runtime_mode_state`. |
| `title` | `TEXT NOT NULL` | |
| `program` | `TEXT NOT NULL DEFAULT ''` | Academic program/context; optional. |
| `status` | `TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('planning','active','writing','revision','defense_ready','completed'))` | Whole-dissertation lifecycle label, independent of chapter statuses. |
| `current_focus` | `TEXT NOT NULL DEFAULT ''` | Manually settable narrative; sensitive-guarded. |
| `created_at`, `updated_at` | `TEXT NOT NULL` | |

Responsibility: the one root record `get_overview()` anchors to.
Uniqueness: the `id = 1` `CHECK` is the uniqueness constraint (only row
`1` can ever exist). No indexes needed (single row). Lifecycle: created
once via `DissertationService.initialize_workspace(title, program, actor)`
(idempotent — a second call updates the existing row rather than erroring,
matching the `NightShiftService.initialize()` "insert if absent" pattern
for its own singleton tables). Cannot reuse an existing model: nothing in
the codebase represents "the one root entity a set of chapters belongs to."

### 3.2 `dissertation_sources` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | |
| `title` | `TEXT NOT NULL` | |
| `source_type` | `TEXT NOT NULL CHECK (source_type IN ('book','journal_article','thesis','conference_paper','report','website','dataset','other'))` | |
| `citation_text` | `TEXT NOT NULL` | User-supplied formatted citation; sensitive-guarded, bounded (2,000 chars). |
| `locator` | `TEXT` | Nullable; URL/DOI/ISBN/Drive-file-id; bounded (500 chars), sensitive-guarded. |
| `status` | `TEXT NOT NULL DEFAULT 'unread' CHECK (status IN ('unread','reviewed','cited','rejected'))` | |
| `created_at`, `updated_at` | `TEXT NOT NULL` | |

Relationships: many-to-many with chapters via 3.3. Referenced `NOT NULL`
by `dissertation_evidence.source_id`. Index: `idx_dissertation_sources_status`
on `status` (list "unread sources" style queries). Cannot reuse an existing
model: no bibliographic-metadata table exists anywhere in NOVA.

### 3.3 `dissertation_source_chapter_links` (new)

| Column | Type | Notes |
|---|---|---|
| `source_id` | `INTEGER NOT NULL REFERENCES dissertation_sources(id) ON DELETE CASCADE` | |
| `chapter_id` | `INTEGER NOT NULL REFERENCES dissertation_chapters(id) ON DELETE CASCADE` | |
| `created_at` | `TEXT NOT NULL` | |

`PRIMARY KEY (source_id, chapter_id)` — uniqueness is the composite key
itself (linking the same pair twice is a no-op, not an error, at the
service layer). Index: none needed beyond the PK (covers both directions
well enough at this data scale — a single dissertation's source count is
small).

### 3.4 `dissertation_evidence` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | |
| `source_id` | `INTEGER NOT NULL REFERENCES dissertation_sources(id) ON DELETE RESTRICT` | `RESTRICT`, not `CASCADE` — deleting a source that has evidence attached must fail explicitly rather than silently orphan-then-delete evidence; §9's provenance requirement. |
| `chapter_id` | `INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL` | Nullable. |
| `gap_id` | `INTEGER REFERENCES dissertation_gaps(id) ON DELETE SET NULL` | Nullable. |
| `summary` | `TEXT NOT NULL` | The extracted finding/claim, sensitive-guarded, **bounded to 1,000 chars** — deliberately too short to be a full-text copy. |
| `locator_detail` | `TEXT` | Nullable, e.g. `"p. 45"` / `"para 3"`; bounded (200 chars). |
| `created_at`, `updated_at` | `TEXT NOT NULL` | |

Index: `idx_dissertation_evidence_source_id`, `idx_dissertation_evidence_chapter_id`.
Cannot reuse `dissertation_notes` or `dissertation_sources`: evidence is
neither bibliographic metadata nor a loose note — it is the specific
provenance-bearing unit Scenario C requires.

### 3.5 `dissertation_notes` (new — "Dissertation Research Notes")

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | |
| `chapter_id` | `INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL` | Nullable. |
| `source_id` | `INTEGER REFERENCES dissertation_sources(id) ON DELETE SET NULL` | Nullable. |
| `evidence_id` | `INTEGER REFERENCES dissertation_evidence(id) ON DELETE SET NULL` | Nullable. |
| `gap_id` | `INTEGER REFERENCES dissertation_gaps(id) ON DELETE SET NULL` | Nullable. |
| `note_type` | `TEXT NOT NULL CHECK (note_type IN ('analysis','synthesis','supervisor_feedback','question','other'))` | |
| `content` | `TEXT NOT NULL` | Sensitive-guarded, bounded (4,000 chars). |
| `created_at`, `updated_at` | `TEXT NOT NULL` | |
| — | `CHECK (chapter_id IS NOT NULL OR source_id IS NOT NULL OR evidence_id IS NOT NULL OR gap_id IS NOT NULL)` | At least one relationship required — this is what distinguishes it from Workspace Memory's unrelated `Note`. |

Index: `idx_dissertation_notes_chapter_id`. Boundary vs. general NOVA
memory: see AD-5.

### 3.6 `dissertation_gaps` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | |
| `chapter_id` | `INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL` | Nullable — a gap can be dissertation-wide. |
| `description` | `TEXT NOT NULL` | Sensitive-guarded, bounded (1,000 chars). |
| `gap_type` | `TEXT NOT NULL CHECK (gap_type IN ('missing_evidence','conceptual_weakness','literature_gap','methodological_question','validation_needed','supervisor_feedback','other'))` | |
| `status` | `TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','resolved','deferred'))` | |
| `priority` | `TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','critical'))` | |
| `next_action` | `TEXT NOT NULL DEFAULT ''` | Manually settable free text, sensitive-guarded. |
| `resolution_note` | `TEXT NOT NULL DEFAULT ''` | Sensitive-guarded; set when transitioning to `resolved`. |
| `resolved_at` | `TEXT` | Nullable. |
| `created_at`, `updated_at` | `TEXT NOT NULL` | |

Transition matrix (enforced in the repository, CAS-guarded like
`dissertation_review_jobs`):
`open → {in_progress, deferred, resolved}`, `in_progress → {resolved,
deferred, open}`, `deferred → {open, in_progress}`, `resolved → {}`
(terminal). Resolution provenance is retained via `resolution_note` +
`resolved_at` plus the existing queryability of
`dissertation_evidence.gap_id`/`dissertation_notes.gap_id` (what
resolved it is discoverable by querying evidence/notes pointing at this
gap — no reverse-FK column needed). Index:
`idx_dissertation_gaps_status`, `idx_dissertation_gaps_chapter_id`.

### 3.7 `dissertation_research_task_links` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | |
| `chapter_id` | `INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL` | Nullable. |
| `gap_id` | `INTEGER REFERENCES dissertation_gaps(id) ON DELETE SET NULL` | Nullable. |
| `work_item_id` | `TEXT NOT NULL` | Control Tower `control_tower_work_items.item_id`. **Not a real SQLite `REFERENCES`** — `app/control_tower/schema.py` is a separate, off-limits file (§11); this is the same "generalized string reference, not a hard FK" pattern Wave 3's `dispatches.source_id` already uses for exactly this cross-domain-without-cross-file-coupling reason. |
| `created_at` | `TEXT NOT NULL` | |
| — | `UNIQUE(work_item_id)` | One dissertation link per Control Tower work item. |

Index: `idx_dissertation_task_links_chapter_id`. Cannot reuse an existing
model: this is *only* the linkage; the task itself is Control Tower's.

### 3.8 `dissertation_decision_links` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | |
| `chapter_id` | `INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL` | Nullable. |
| `decision_id` | `TEXT NOT NULL` | Control Tower `control_tower_decisions.decision_id`; same string-reference rationale as 3.7. |
| `created_at` | `TEXT NOT NULL` | |
| — | `UNIQUE(decision_id)` | |

### 3.9 `dissertation_research_audit_log` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | |
| `target_type` | `TEXT NOT NULL CHECK (target_type IN ('workspace','source','evidence','note','gap','research_task_link','decision_link'))` | Deliberately a **new, separate** table from `dissertation_revision_log` rather than widening that table's `CHECK` — SQLite cannot `ALTER` a `CHECK` constraint in place, and a destructive table-rebuild migration is forbidden by §9/§10. Mirrors the existing repo-wide pattern of one audit table per domain slice. |
| `target_id` | `INTEGER NOT NULL` | |
| `event` | `TEXT NOT NULL` | |
| `actor` | `TEXT NOT NULL` | |
| `detail` | `TEXT NOT NULL DEFAULT ''` | Sensitive-guarded. |
| `created_at` | `TEXT NOT NULL` | |

Index: `idx_dissertation_research_audit_target` on `(target_type,
target_id)`.

### 3.10 Additive columns on `dissertation_chapters`

```sql
ALTER TABLE dissertation_chapters ADD COLUMN current_focus TEXT NOT NULL DEFAULT '';
```
Applied via the same `PRAGMA table_info(dissertation_chapters)` existence
check already used for `version_state` — never a blind
`try/except sqlite3.OperationalError`.

---

## 4. Service/API Contract

### 4.1 Repository layer (`DissertationRepository`, extended)

New methods, same conventions as existing ones (parameterized SQL only,
`_many()` helper reused, CAS via `rowcount` checks for status transitions,
`Not FoundError`/`InvalidDissertationValueError` on bad input):

```python
# Workspace root
def get_or_create_workspace(self, title: str, program: str) -> DissertationWorkspace: ...
def update_workspace(self, *, status: str | None = None, current_focus: str | None = None) -> DissertationWorkspace: ...

# Chapter extension
def update_chapter_focus(self, chapter_id: int, current_focus: str) -> Chapter: ...

# Sources
def create_source(self, title: str, source_type: str, citation_text: str, locator: str | None) -> Source: ...
def update_source_status(self, source_id: int, status: str) -> Source: ...
def link_source_to_chapter(self, source_id: int, chapter_id: int) -> None: ...  # idempotent
def list_sources(self, *, chapter_id: int | None = None) -> list[Source]: ...
def get_source(self, source_id: int) -> Source: ...

# Evidence
def create_evidence(self, source_id: int, summary: str, *, chapter_id: int | None, gap_id: int | None, locator_detail: str | None) -> Evidence: ...
def list_evidence(self, *, chapter_id: int | None = None, gap_id: int | None = None, source_id: int | None = None) -> list[Evidence]: ...
def get_evidence(self, evidence_id: int) -> Evidence: ...

# Notes
def create_note(self, note_type: str, content: str, *, chapter_id=None, source_id=None, evidence_id=None, gap_id=None) -> ResearchNote: ...
def list_notes(self, *, chapter_id: int | None = None, gap_id: int | None = None) -> list[ResearchNote]: ...

# Gaps
def create_gap(self, description: str, gap_type: str, *, chapter_id: int | None, priority: str = "normal") -> Gap: ...
def update_gap_status(self, gap_id: int, status: str, *, resolution_note: str = "") -> Gap: ...  # CAS, transition-matrix-guarded
def update_gap_next_action(self, gap_id: int, next_action: str) -> Gap: ...
def list_gaps(self, *, chapter_id: int | None = None, status: str | None = None) -> list[Gap]: ...

# Cross-domain links (no Control Tower call here — repository only persists the link row)
def create_research_task_link(self, work_item_id: str, *, chapter_id: int | None, gap_id: int | None) -> ResearchTaskLink: ...
def list_research_task_links(self, *, chapter_id: int | None = None) -> list[ResearchTaskLink]: ...
def create_decision_link(self, decision_id: str, *, chapter_id: int | None) -> DecisionLink: ...
def list_decision_links(self, *, chapter_id: int | None = None) -> list[DecisionLink]: ...

# Audit
def audit(self, target_type: str, target_id: int, event: str, actor: str, detail: str = "") -> None: ...
```

### 4.2 Service layer (`DissertationService`, extended)

Constructor gains one optional collaborator, matching the established
optional-injection pattern:

```python
def __init__(self, database: MemoryDatabase, *, control_tower: "ControlTowerService | None" = None) -> None: ...
```

New public methods (all validate + sensitive-content-guard free text before
calling the repository, matching every existing method's style):

```python
def initialize_workspace(self, title: str, program: str, actor: str) -> DissertationWorkspace: ...
def set_current_focus(self, current_focus: str, actor: str, *, chapter_id: int | None = None) -> None: ...
    # chapter_id=None targets the workspace root; otherwise targets that chapter.

def create_source(self, title: str, source_type: str, citation_text: str, actor: str, *, locator: str | None = None, chapter_id: int | None = None) -> Source: ...
def list_sources(self, *, chapter_id: int | None = None) -> list[Source]: ...

def create_evidence(self, source_id: int, summary: str, actor: str, *, chapter_id: int | None = None, gap_id: int | None = None, locator_detail: str | None = None) -> Evidence: ...
def list_evidence(self, *, chapter_id: int | None = None, gap_id: int | None = None) -> list[Evidence]: ...

def create_note(self, note_type: str, content: str, actor: str, *, chapter_id=None, source_id=None, evidence_id=None, gap_id=None) -> ResearchNote: ...

def create_gap(self, description: str, gap_type: str, actor: str, *, chapter_id: int | None = None, priority: str = "normal") -> Gap: ...
def resolve_gap(self, gap_id: int, resolution_note: str, actor: str) -> Gap: ...
def update_gap_status(self, gap_id: int, status: str, actor: str, *, resolution_note: str = "") -> Gap: ...
def list_gaps(self, *, chapter_id: int | None = None, status: str | None = None) -> list[Gap]: ...

def create_research_task(self, title: str, actor: str, *, chapter_id: int | None = None, gap_id: int | None = None, summary: str | None = None, urgency: int = 0, importance: int = 0) -> ResearchTaskLink: ...
    # Raises DissertationConfigurationError if self.control_tower is None.
    # Delegates entirely to self.control_tower.capture_work(category="academic", ...);
    # persists only the local link.
def list_research_tasks(self, *, chapter_id: int | None = None) -> list[dict]: ...
    # Resolves each link's work_item_id through Control Tower and returns merged view objects.

def create_decision_link(self, summary: str, rationale: str, impact: str, approved_by: str, actor: str, *, chapter_id: int | None = None) -> DecisionLink: ...
def list_decisions(self, *, chapter_id: int | None = None) -> list[dict]: ...

def get_overview(self) -> DissertationOverview: ...
    # Computed: workspace + per-chapter summaries + overall progress +
    # open gap count + pending task count + current_focus + next_action
    # (via get_next_action(chapter_id=None)).
def get_chapter_detail(self, chapter_id: int) -> ChapterDetail: ...
    # Computed: chapter + progress + sources + evidence + open gaps +
    # pending tasks + current_focus + next_action (via get_next_action(chapter_id=...)).
def get_next_action(self, *, chapter_id: int | None = None) -> str: ...
    # Pure function per AD-9's cascade. No LLM call, no I/O beyond the
    # already-loaded domain state.
```

New DTOs in `models.py`: `DissertationWorkspace`, `Source`, `Evidence`,
`ResearchNote`, `Gap`, `ResearchTaskLink`, `DecisionLink`,
`DissertationOverview`, `ChapterDetail` (the last two are read-model
dataclasses composed from the others — not persisted).

### 4.3 Telegram/controller surface (`app/telegram_bot.py`, extended)

New accessor (mirrors `_control_tower`'s style — returns `None` rather than
raising, since dissertation handlers should degrade the same way Control
Tower's own handlers do):

```python
def _dissertation(context: ContextTypes.DEFAULT_TYPE) -> DissertationService | None:
    service = context.application.bot_data.get("dissertation")
    return service if isinstance(service, DissertationService) else None
```

One new command, `dissertation_handler`, dispatching on
`context.args[0]` (all business logic stays in `DissertationService` —
the handler only parses args, calls one service method, and formats the
reply via `_bounded_message`, exactly like `capture_handler`/`today_handler`):

| Args | Calls | Answers (§5 question) |
|---|---|---|
| *(none)* | `get_overview()` | "Status disertasi saya bagaimana?" |
| `chapter <n>` | `get_chapter_detail(...)` (resolve `<n>` = `order_index` → `chapter_id`) | "Bab II sudah sampai mana?" |
| `gaps` | `list_gaps(status=None)` filtered client-side to non-resolved | "Apa gap penelitian yang masih terbuka?" |
| `next` | `get_next_action()` | "Apa tugas akademik berikutnya?" |
| `tasks` | `list_research_tasks()` | (supporting detail for the same question) |
| `evidence <n>` | `list_evidence(chapter_id=...)` | "Evidence apa yang mendukung Bab II?" |
| `sources [n]` | `list_sources(chapter_id=...)` or all | "Sumber apa saja yang terkait dengan topik tertentu?" |
| `decisions` | `list_decisions()` | "Apa keputusan penting yang sudah dibuat?" |

`build_application()` gains one new parameter, `dissertation:
DissertationService | None = None`; `bot_data["dissertation"]` is always
set (even to `None`), matching `execution`/`night_shift`/`control_tower`'s
existing always-set convention (not the newer `if x is not None:`
conditional style Wave 3 used for `dispatch_svc`/`approval_svc` — this
sprint follows the *older*, more established convention since `dissertation`
was already being constructed as an always-present optional service before
Wave 3 existed).

### 4.4 External adapter responsibilities

None. Sprint 6A introduces zero new adapters. `locator` fields are inert
text; no code path resolves them against any external system.

---

## 5. File-Level Implementation Plan

| File | Action | Responsibility | Reason |
|---|---|---|---|
| `app/dissertation/schema.py` | Modify (additive) | Add 8 new `CREATE TABLE IF NOT EXISTS` blocks + `CHAPTER_FOCUS_MIGRATION` guarded `ALTER TABLE` + new indexes | §3 |
| `app/dissertation/models.py` | Modify (additive) | Add 9 new frozen dataclasses (`DissertationWorkspace`, `Source`, `Evidence`, `ResearchNote`, `Gap`, `ResearchTaskLink`, `DecisionLink`, `DissertationOverview`, `ChapterDetail`) | §4.2 |
| `app/dissertation/repository.py` | Modify (additive) | Add methods in §4.1; add `GAP_STATUSES`/`GAP_TRANSITIONS`/`SOURCE_TYPES`/`NOTE_TYPES` constants next to the existing `CHAPTER_STATUSES`-style constants | §4.1 |
| `app/dissertation/service.py` | Modify (additive) | Add methods in §4.2; add `control_tower` optional constructor param; add `get_overview`/`get_chapter_detail`/`get_next_action` computed read-models | §4.2, AD-9 |
| `app/dissertation/errors.py` | **New** | `DissertationConfigurationError` (raised when a Control-Tower-dependent method is called with `control_tower=None`) — kept separate from `repository.py`'s existing `DissertationError` family only if the existing file's error hierarchy is reused instead is equally acceptable; Codex should add it to `repository.py` alongside the existing error classes rather than create a new file, to match 6A.0's existing convention of one error module co-located with the repository. *(Correction to the "new file" instinct — see Codex Implementation Order item 2.)* | Consistency |
| `app/main.py` | Modify | **Reorder**: move the existing `dissertation = DissertationService(...)` construction block to *after* the `control_tower` block (which itself must remain after the dispatch/approval block), and pass `control_tower=control_tower` into the constructor. Add `dissertation` to the `build_application(...)` call. | AD-6/AD-7 require `control_tower` to exist first; today's order has `dissertation` constructed too early. |
| `app/telegram_bot.py` | Modify (additive) | `_dissertation` accessor, `dissertation_handler`, `build_application()` new param + `bot_data` entry, `HELP_MESSAGE` line, one `CommandHandler("dissertation", dissertation_handler)` registration | §4.3 |
| `docs/dissertation-workspace.md` | Modify (additive) | Add sections for the 8 new entities, the progress formula, the next-action cascade, and the Control-Tower-linkage design; do not rewrite the 6A.0 sections | §17 |
| `docs/SPRINT_6A.md` | This file | Frozen specification | — |
| `docs/CURRENT_SPRINT.md` | Modify (by Codex, at merge time — not by this spec) | Add the Sprint 6A entry under a new `## Wave 4` (or continued Wave 3, per whatever the repo's wave-numbering convention is by then) heading | §17 proposal below |
| `tests/test_dissertation_schema.py` | Modify (additive) | New table creation, idempotent re-init, new column guard, cascade/`SET NULL`/`RESTRICT` behavior | §7 |
| `tests/test_dissertation_repository.py` | Modify (additive) | CRUD + relationships for every new table, gap transition matrix, `CHECK` enforcement | §7 |
| `tests/test_dissertation_service.py` | Modify (additive) | Validation, sensitive-content rejection, `get_overview`/`get_chapter_detail`/`get_next_action` behavior, Control-Tower delegation (via a fake/mock `ControlTowerService`) | §7 |
| `tests/test_telegram_dissertation.py` | **New** | All 8 subviews, accessor, missing-service degradation, command registered once, `HELP_MESSAGE` updated, no business logic reachable without going through the service | §7 |
| `tests/test_dissertation_security.py` | **New** | Sensitive-content rejection across every new free-text field; no raw content ever persisted; evidence `summary` length bound enforced | §9 |

---

## 6. Migration Plan

- Every new table: `CREATE TABLE IF NOT EXISTS`. Every new index:
  `CREATE INDEX IF NOT EXISTS`. Zero `DROP`, zero destructive `ALTER`.
- The one new column (`dissertation_chapters.current_focus`) uses the
  exact `PRAGMA table_info(...)` existence-check pattern already proven by
  `VERSION_STATE_MIGRATION` in the same file — applied inside the same
  `apply_schema()` function, not a separate migration runner (this repo has
  none, by design — see the Wave 3 contract's schema-conventions notes).
- `apply_schema()` must remain safe to call twice (tested).
- Existing data behavior: every pre-6A row in `dissertation_chapters`,
  `dissertation_subchapters`, `dissertation_document_versions`,
  `dissertation_paragraph_maps`, `dissertation_review_jobs`,
  `dissertation_revision_log` is untouched; the new `current_focus` column
  defaults to `''` for all pre-existing chapter rows.
- No table anywhere in this plan is renamed or restructured.

---

## 7. Test Plan

| File | Cases |
|---|---|
| `test_dissertation_schema.py` | All 8 new tables created idempotently; `apply_schema()` twice is a no-op; `current_focus` column added exactly once even if called twice; FK behaviors: deleting a chapter `SET NULL`s evidence/notes/gaps/links but does not delete them; deleting a source with attached evidence raises (`RESTRICT`) rather than cascading; `dissertation_source_chapter_links` composite PK rejects duplicate insert cleanly (service-layer no-op, not a raw `IntegrityError` leak). |
| `test_dissertation_repository.py` | CRUD for sources/evidence/notes/gaps/links; gap transition matrix (`open→resolved` legal, `resolved→open` illegal, CAS rejects concurrent change); `dissertation_notes` `CHECK` rejects a note with all four relationship columns null; `list_*` filters by `chapter_id`/`gap_id`/`source_id` correctly; `dissertation_research_task_links`/`decision_links` `UNIQUE(work_item_id)`/`UNIQUE(decision_id)` enforced. |
| `test_dissertation_service.py` | Sensitive-content rejected on every new free-text field (title, citation_text, locator, summary, content, description, resolution_note, current_focus) before any DB write; evidence `summary` over 1,000 chars rejected; `create_research_task`/`create_decision_link` raise a typed error when `control_tower=None`; with a fake `ControlTowerService`, `create_research_task` delegates `category="academic"` and persists the correct link; `get_overview`/`get_chapter_detail` progress math matches AD-8's formula exactly for a hand-constructed fixture (0/50/75/100 chapters, subchapter averaging); `get_next_action` cascade order verified with fixtures exercising each of the 6 rules in isolation, and a fixture proving rule 1 (critical gap) wins over rule 2 (pending task) when both are true simultaneously. |
| `test_telegram_dissertation.py` | Each of the 8 subviews produces the right service call and a bounded, non-empty reply; unauthorized user rejected; missing `dissertation` service degrades to a sanitized "temporarily unavailable" reply, not a stack trace; `/dissertation` registered exactly once; `HELP_MESSAGE` contains the command; existing commands remain registered (reuse the existing `test_build_application_registers_each_control_tower_command_once`-style walk). |
| `test_dissertation_security.py` | Parametrized sensitive-input rejection (api_key=, ssh-rsa, `-----BEGIN`, credential-JSON) across every new free-text field, confirming nothing is written to any new table and nothing appears in `dissertation_research_audit_log.detail`. |
| Full regression | `python -m pytest -q` must report `492 + <new count>` passed, zero failures, zero prior tests broken. |

Behavioral verification, not just pass/fail: every new test above asserts
on *returned values* (exact progress percentages, exact next-action
strings, exact filtered lists) — not merely "no exception raised."

---

## 8. Security Review

- **Secrets**: no new field anywhere accepts or stores credentials;
  `SENSITIVE_CONTENT_PATTERN` (existing, reused) screens every new
  free-text field before persistence, matching 6A.0's own established
  discipline exactly.
- **Sensitive content**: `citation_text`, `locator`, evidence `summary`,
  note `content`, gap `description`/`resolution_note`/`next_action`,
  `current_focus` — all guarded, all length-bounded (specific bounds in
  §3), preventing this domain from becoming an unreviewed place for large
  raw text to land (mirrors 6A.0's own stated rationale for never storing
  raw document content).
- **External operations**: zero. No shell, no subprocess, no network call,
  no Google API call, no Git action anywhere in this sprint's code (same
  guarantee 6A.0 already has, extended to the new tables).
- **Logging**: no new log statement includes free-text content; existing
  domain convention (log event/actor/entity id only) is followed.
- **Approval boundaries**: `create_research_task`/`create_decision_link`
  never bypass Control Tower's own validation (`capture_work`/
  `register_decision` still enforce their own category/validation rules
  unchanged) — Sprint 6A adds no new approval-adjacent capability of its
  own, and never touches `app/dispatch/`'s approval machinery.
- **Destructive actions**: none possible — every write in this sprint is
  additive-row or status-transition; no delete-by-default capability is
  exposed, and the one cascading deletes that exist (`ON DELETE CASCADE`
  on `dissertation_source_chapter_links`, chapter/subchapter version
  cleanup) were already present in 6A.0's design or are the narrowly-scoped
  link-table cleanup described in §3.

---

## 9. Acceptance Criteria

- [ ] All 8 new tables plus the `current_focus` column are created
      idempotently; `apply_schema()` callable twice with no error or
      duplication; zero existing table/column altered or dropped.
- [ ] `DissertationService.get_overview()` returns overall status,
      per-chapter status/progress, overall progress (per AD-8's exact
      formula), open-gap count, pending-task count, current focus, and
      next action, for a fixture dissertation with ≥2 chapters
      (Scenario A).
- [ ] `DissertationService.get_chapter_detail(chapter_id)` returns
      progress, linked sources, linked evidence, unresolved gaps, pending
      tasks, and next action for one chapter (Scenario B).
- [ ] Evidence created without a valid `source_id` is rejected before any
      row is written; every evidence item is resolvable to exactly one
      source, and optionally to a chapter/note (Scenario C).
- [ ] A gap can move `open → in_progress → resolved` (and the other legal
      edges); an illegal edge (e.g. `resolved → open`) is rejected;
      resolving records a `resolution_note` and `resolved_at` (Scenario D).
- [ ] `get_next_action()` returns a correct, deterministic string with zero
      model calls, verified against fixtures for all 6 cascade rules,
      including the "critical gap beats pending task" ordering case
      (Scenario E).
- [ ] Full regression is `492 + N` passed (`N` = new Sprint 6A test count),
      zero regressions, confirmed by running the actual suite — not
      assumed (Scenario F).
- [ ] `create_research_task`/`create_decision_link` correctly delegate to
      `ControlTowerService.capture_work`/`register_decision` with
      `category="academic"`, and raise a typed, sanitized error when no
      `control_tower` collaborator was injected.
- [ ] All 8 `/dissertation` subviews are reachable, registered exactly
      once, and each answers its corresponding §5 question with a bounded,
      sanitized, non-database-dump reply.
- [ ] No sensitive-shaped input (parametrized: API key, SSH key, PEM
      block, credential JSON) can be persisted through any new field —
      verified directly, not inferred.
- [ ] `app/dispatch/`, `app/nightshift/`, `app/execution/`,
      `app/google_workspace/`, `app/control_tower/repository.py`,
      `app/control_tower/models.py`, `app/control_tower/schema.py` are
      untouched by this sprint's diff (`git diff --stat` confirms).

---

## 10. Out-of-Scope Confirmation

Explicitly not built in Sprint 6A (per §14 of the brief, reaffirmed):
full document generation/automatic chapter writing, automatic Google Docs
editing, supervisor email workflows, journal submission, Scopus scraping,
a Zotero replacement, a citation-formatting engine, plagiarism checking,
autonomous literature-review agents, autonomous internet research, a vector
database/RAG platform, a UI dashboard, a mobile app, multi-user
collaboration, publication workflow, and any Sprint 6B functionality
(dispatch-driven research automation). Additionally, and specific to this
spec's own scope narrowing: **Telegram write commands** (capturing a new
source/gap/evidence/note via a slash command) are deferred — the service
layer supports it fully; the Telegram surface does not expose it yet, by
deliberate choice (AD-10). **Subchapter-level `current_focus`** is
deferred (AD-3). **Indonesian natural-language routing** through
`app/natural_language.py` is deferred — §5's Indonesian questions are
answered by the `/dissertation` command family's replies, not by parsing
Indonesian free text into intents this sprint.

---

## 11. Risks / Technical Debt

**Must fix in 6A:**
- None outstanding — every requirement in §4 of the brief has a
  corresponding table/method in this spec.

**May defer (explicitly, documented above, not silent gaps):**
- Telegram write commands for sources/gaps/evidence/notes (AD-10).
- Subchapter-level `current_focus`/progress granularity (AD-3).
- Free-text source search (`/dissertation sources <text filter>`) — V1
  only filters by chapter number or lists all; no substring/fuzzy search.
- Indonesian natural-language intent routing.
- `dissertation_chapters`/`dissertation_subchapters` still have no
  `CHECK`-enforced status transition matrix (pre-existing 6A.0 debt, not
  introduced or worsened by this sprint, not fixed by it either — fixing
  it would change 6A.0's already-shipped, already-tested behavior, which
  is out of this sprint's charter).

**Explicitly belongs to 6B+:**
- Any Dispatch/Night Shift-driven automated research work (AD-12's seam).
- Real AI-assisted evidence extraction, drafting, or review execution
  (`dissertation_review_jobs` remains a lifecycle placeholder with no
  executor, exactly as 6A.0 left it).
- Any Google Drive read/write integration for sources.
- Multi-chapter-weighted or milestone-based progress models, if the simple
  mean in AD-8 proves insufficient in practice.

---

## 12. Codex Implementation Order

1. `app/dissertation/schema.py` — add the 8 tables, the `current_focus`
   migration, and all indexes. Run `test_dissertation_schema.py`
   (extended first, per TDD-appropriate order) before touching anything
   else.
2. `app/dissertation/models.py` — add the 9 new dataclasses.
3. `app/dissertation/repository.py` — add the new error classes
   (`InvalidGapTransitionError`, extending the existing `DissertationError`
   hierarchy already in this file — do **not** create a separate
   `errors.py`) alongside the new repository methods from §4.1, plus
   `GAP_STATUSES`/`GAP_TRANSITIONS`/`SOURCE_TYPES`/`NOTE_TYPES` constants.
4. `app/dissertation/service.py` — add the `control_tower` optional
   constructor param and every method in §4.2, in this order: workspace →
   chapter focus → sources → evidence → notes → gaps → research task/
   decision links → `get_overview`/`get_chapter_detail`/`get_next_action`
   last (they depend on everything else).
5. `tests/test_dissertation_repository.py` and
   `tests/test_dissertation_service.py` — extend fully before touching
   Telegram; confirm `python -m pytest tests/test_dissertation_*.py` is
   green.
6. `tests/test_dissertation_security.py` — new file, parametrized
   sensitive-content rejection sweep.
7. `app/main.py` — reorder the `dissertation` construction block to after
   `control_tower`, inject `control_tower=control_tower`, add
   `dissertation` to the `build_application(...)` call.
8. `app/telegram_bot.py` — `_dissertation` accessor, `dissertation_handler`
   with the 8-subview dispatch table, `build_application()` param +
   `bot_data` entry, `HELP_MESSAGE` line, one `CommandHandler` registration.
9. `tests/test_telegram_dissertation.py` — new file.
10. `docs/dissertation-workspace.md` — append new sections (do not rewrite
    6A.0's sections).
11. Full regression: `python -m pytest -q`. Confirm `492 + N` passed, `N`
    matching the sum of new tests added in steps 1–9.
12. `python -m py_compile app/dissertation/*.py app/main.py
    app/telegram_bot.py`; `git diff --check`; `git status --short`;
    `git diff --stat` — confirm only the files in §5's table changed.
13. Stop before commit/push, per the standing NOVA constraint — hand back
    for cooperative review.

---

## 13. Final Verdict

**READY FOR CODEX IMPLEMENTATION**

Every entity, relationship, state model, formula, and file-level change in
this spec is fully determined — nothing is left for Codex to invent. The
two systems most at risk of duplication (tasks, decisions) are resolved by
reference to Control Tower rather than new tables; the one system genuinely
missing (a dissertation root, source/evidence/gap/note structure) is scoped
to the minimum additive schema that satisfies every §13 acceptance
scenario without expanding into any §14 out-of-scope territory.
