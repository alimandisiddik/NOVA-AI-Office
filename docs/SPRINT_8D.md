# Sprint 8D — Workspace → Control Tower Integration

## Status: **FROZEN** — architecture decisions applied. Revised per Control
Tower Freeze Review: provenance identity now anchored on stable external
identifiers (message/event/file ID, scoped to the configured account)
rather than a content hash alone. Further revised per a final Control Tower
correction: (1) `account_namespace` is sourced from a new, dedicated 8A
method exposing **authenticated Google account identity** — never the
OAuth client identity a prior draft mistakenly reused; (2) **8D no longer
edits `app/main.py`/`build_application()`'s signature directly** — that
wiring is now owned by the Stage 3 (G3) integration branch, the same
isolation principle Stage 1 already applies.

See `docs/WAVE_7_SHARED_CONTRACTS.md` §3.5, §3.7, §4 (AD-W7-10, AD-W7-17),
§5a, §11 for the cross-sprint contract this spec implements.

## 1. Objective

Translate Workspace signals (emails, calendar events, documents) into
canonical NOVA workflow candidates — `WorkItem`, `Decision`, `KnowledgeItem`
— without ever becoming a second source of truth for any of them, and
without ever collapsing two distinct real Workspace objects into one
candidate merely because their content is similar.

## 2. User-visible usable capability

NOVA can look at an inbox item, meeting, or document and propose "this
looks like it should become a WorkItem/Decision/KnowledgeItem," show the
user exactly what it would create, and — only on explicit confirmation —
create it through the real, existing canonical service. Nothing is created
silently. Proposing from the same real email, event, or file twice never
creates a duplicate candidate; proposing from two *different* real objects
that happen to contain similar text always creates two distinct candidates.

## 3. Scope

- New `app/workspace_bridge/` package: `models.py`, `schema.py`,
  `repository.py`, `service.py`.
- `WorkspaceSourceRef` per §8.
- `WorkspaceBridgeService`:
  - `propose_from_message(message: MessageSummary, actor: str) ->
    WorkspaceSourceRef` — deterministic candidate extraction (date-like
    phrases → deadline candidate; imperative sentence + subject → WorkItem
    candidate; otherwise → KnowledgeItem candidate), `status='candidate'`,
    `target_type`/`target_id` still `NULL`. Identity is derived from
    `message.message_id` (§8) — never from message content.
  - `propose_from_meeting(brief: MeetingBrief, actor: str) ->
    WorkspaceSourceRef` — same discipline, sourced from Calendar via 8A/8B;
    identity derived from the event's stable ID.
  - `propose_from_document(metadata: "DriveFileMetadata | DocumentContent",
    actor: str) -> WorkspaceSourceRef` — same discipline, sourced from a
    Drive/Docs read (8A) the caller already holds; identity derived from
    `file_id`. Produces a `knowledge_item`-leaning candidate by default,
    though `commit()` still accepts any `target_type` the user actually
    chooses.
  - `commit(ref_id, target_type, actor, **fields) -> WorkspaceSourceRef` —
    the **only** method allowed to call `ControlTowerService`'s existing
    capture method / `KnowledgeService.create_source()`+`create_item()` on
    behalf of a candidate (mirrors 7D's `start_execution()`-is-the-only-
    caller discipline). Writes `WorkspaceSourceRef.target_id`/
    `status='committed'` in the same transaction as the downstream domain's
    own write succeeding; on downstream failure, the candidate row is left
    `candidate` (no partial commit).
  - `dismiss(ref_id, actor, reason) -> WorkspaceSourceRef`.
- New read-only Telegram commands: `/workspacecandidates`,
  `/workspacecommit <ref_id> <work_item|decision|knowledge_item>`.

## 4. Out of scope

- Any automatic, unconfirmed commit — human ambiguity always routes to a
  question, never a guess.
- Persisting the full raw email/event/document payload — only the stable
  external identifier (§8) and the small, already-bounded fields needed to
  render the candidate for user review.
- A second `WorkItem`/`Decision`/`KnowledgeItem` table — `commit()` always
  calls the real, existing canonical service.
- Editing `app/control_tower/**` or `app/knowledge/**` beyond the one
  additive enum widening noted in §8.
- Using content similarity as a substitute for real object identity —
  identical text from two different messages/events/files is never treated
  as "the same candidate" (AD-W7-17).

## 5. Existing architecture reused

- `app.control_tower.service.ControlTowerService` — existing public
  work-item capture method, invoked only from `commit()`.
- `app.knowledge.service.KnowledgeService.create_source()`/`create_item()` —
  invoked only from `commit()`.
- **8A's new `GoogleAuthenticator.get_account_namespace()` method** (added
  by 8A per `docs/WAVE_7_SHARED_CONTRACTS.md` AD-W7-17 and
  `docs/SPRINT_8A.md` §3/§5) — the sole, correct source of
  `account_namespace` (§8). 8D does **not** use `client_id_hash` for this —
  that value identifies the OAuth client/application, not the authenticated
  Google account, and using it would let two different Google accounts
  authorized through the same NOVA installation collide into one
  `account_namespace`. 8D calls `get_account_namespace()` and treats its
  output as an opaque string; it does not know or care what the underlying
  account identifier is.
- `app.security.SENSITIVE_CONTENT_PATTERN` — applied to every candidate
  field before persistence and again before commit.
- 8F's `ExternalMessageIntake` *pattern* (candidate → committed, opaque
  provenance) — reused conceptually, not as shared code, since 8D and 8F
  must remain independently parallelizable.

## 6. Owned files/modules

- `app/workspace_bridge/{models,schema,repository,service}.py` — new,
  8D-exclusive.
- `app/workspace_bridge/telegram.py` — new, 8D-exclusive: independently
  unit-testable handler functions for `/workspacecandidates`/
  `/workspacecommit`, each reading `context.bot_data.get(
  "workspace_bridge")` and degrading to a clear "unavailable" reply if
  absent (AD-W7-10, generalized to Stage 3 this pass).
- `tests/test_workspace_bridge_*.py` — new, 8D-exclusive.

**Not owned by 8D (see §7 and `docs/WAVE_7_SHARED_CONTRACTS.md` §11):**
`app/main.py`'s `WorkspaceBridgeService` construction, and the
`workspace_bridge` parameter on `build_application()`'s signature — both
owned by the **G3 integration branch**, not by 8D's own branch. This
corrects the prior draft, which allowed 8D to append its own
`build_application()` parameter directly on the (rejected) reasoning that
Stage 3 carried no parallel-signature collision — Control Tower determined
8C and 8D are still two genuinely parallel branches and would face the same
collision Stage 1 already had to solve.

## 7. Shared dependencies

- `app/google_workspace/**` — type-reference only (`MessageSummary`,
  `MeetingBrief`, `DriveFileMetadata`, `DocumentContent`), plus a call to
  `GoogleAuthenticator.get_account_namespace()` (8A, new — §5) for
  `account_namespace`. **Never `client_id_hash`.** No direct Google API
  service call otherwise — candidates are proposed from DTOs the caller
  already holds.
- `app/control_tower/**` — read only for existence checks, write only
  through the existing public capture method, called exclusively from
  `commit()`.
- `app/knowledge/**` — write only through existing public methods, called
  exclusively from `commit()`.
- `app/conversation/**` (8G) — optional, same integration shape as 8F §7;
  `None`-safe fallback to an explicit `/workspacecommit` command otherwise.
- **Shared-file rules (revised per AD-W7-10, generalized this pass):** 8D's
  branch **does not edit** `app/main.py` or add a parameter to
  `build_application()`'s signature — `WorkspaceBridgeService` construction
  and the `workspace_bridge`/`bot_data` wiring are added once, by the **G3
  integration branch**, alongside 8C's equivalent. 8D's branch **may
  optionally** add its own single, isolated `application.add_handler(...)`
  line per command for branch-level end-to-end testability; if it does not,
  G3 adds them.

## 8. Data/contracts — stable external identity (revised)

New additive table, owned entirely by `app/workspace_bridge/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS workspace_source_refs (
    id                    INTEGER PRIMARY KEY,
    source_system         TEXT NOT NULL CHECK (source_system IN ('gmail','calendar','drive')),
    account_namespace     TEXT NOT NULL,
    external_source_type  TEXT NOT NULL CHECK (external_source_type IN ('message','thread','event','file')),
    external_source_id    TEXT NOT NULL,
    content_fingerprint   TEXT,
    candidate_summary     TEXT NOT NULL,
    target_type           TEXT CHECK (target_type IN ('work_item','decision','knowledge_item',NULL)),
    target_id             TEXT,
    status                TEXT NOT NULL DEFAULT 'candidate'
                          CHECK (status IN ('candidate','committed','dismissed')),
    created_by            TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_source_identity
    ON workspace_source_refs(source_system, account_namespace, external_source_type, external_source_id);
CREATE INDEX IF NOT EXISTS idx_workspace_source_status ON workspace_source_refs(status);
CREATE TABLE IF NOT EXISTS workspace_bridge_audit_log (
    id         INTEGER PRIMARY KEY,
    ref_id     INTEGER NOT NULL REFERENCES workspace_source_refs(id) ON DELETE CASCADE,
    event      TEXT NOT NULL,
    actor      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
```

**Identity rule (AD-W7-17, frozen this pass — replaces the prior
`(source_system, content_hash)` design):**

- **Primary identity, database-enforced:** `(source_system,
  account_namespace, external_source_type, external_source_id)`.
  `external_source_id` is the real Gmail message/thread ID, Calendar event
  ID, or Drive/Docs/Sheets/Slides file ID — never derived from content.
  `account_namespace` is obtained from **8A's `GoogleAuthenticator.
  get_account_namespace()`** (§5, §7) — a stable, opaque hash of the
  **authenticated Google account's own identity**. It is **not**
  `client_id_hash`: that value identifies the NOVA OAuth application/client
  and is identical for every account ever authorized through it, which
  would let two different Google accounts collide into one namespace — the
  exact mistake AD-W7-17 corrects this pass. Today there is only one
  configured account at a time, but the field is populated from real
  account identity now, not a placeholder, so a second account authorized
  later cannot silently collide with the first's provenance. A second
  `propose_from_*()` call for the exact same real message/event/file under
  the same account returns the existing row, enforced by the unique index
  above — the deduplication/idempotency mechanism this table actually
  relies on. The same `external_source_id` under two **different**
  accounts is, correctly, two distinct rows (§10).
- **`content_fingerprint` is secondary and non-unique.** It is a sha256 of
  normalized `candidate_summary` text, retained purely as an optional
  informational hint (e.g., a future UI could use it to flag "this
  candidate's text looks similar to another one") — it is never part of
  any uniqueness constraint and never used to decide whether two candidates
  are "the same object." Two different emails with identical subject lines
  produce two different rows, each independently reviewable.

`target_type`/`target_id` remain loose references (no FK), same discipline
as every other cross-domain pointer in this repository. `candidate_summary`
is a short, bounded, already-screened string — never the raw email body/
event description/document content.

**Note on `KnowledgeSource.source_type`:** 7B's existing enum already
includes `drive_file`/`calendar_event`; it does **not** include an
`email`/`gmail_message` value. 8D adds exactly one new value to that
existing `CHECK` constraint (`gmail_message`) via 7B's own additive-
migration pattern — this is the one place 8D touches a file inside
`app/knowledge/`, and it is additive-enum-only.

## 9. Security constraints

- `candidate_summary` is bounded and screened with
  `SENSITIVE_CONTENT_PATTERN` before every write (creation and again
  immediately before `commit()` copies fields into the downstream domain's
  own create call).
- `external_source_id` is opaque (a Gmail message ID / Calendar event ID /
  Drive file ID string) — never a raw URL, never message/document content.
- `account_namespace` is an opaque hash, never a raw account email, subject
  ID, or client secret — obtained exclusively via
  `GoogleAuthenticator.get_account_namespace()` (8A). 8D never computes an
  account identifier itself and never falls back to `client_id_hash` under
  any circumstance.
- `commit()` fails closed on an invalid `target_type` or an already-
  `committed`/`dismissed` row — no partial or duplicate downstream write.
- Telegram errors are sanitized, matching every existing domain's
  convention.

## 10. Tests

- `propose_from_message()`/`propose_from_meeting()`/
  `propose_from_document()` — candidate extraction for each target-type
  signal, sensitive-content rejection.
- **Stable-identity dedup test (explicit):** proposing twice from DTOs
  sharing the same real `message_id`/event ID/`file_id` returns the same
  row, never a duplicate.
- **Distinct-object test (explicit, the concrete case this revision fixes):**
  proposing from two DTOs with *different* `message_id`s but
  byte-identical `candidate_summary` text creates **two distinct rows**,
  each independently committable/dismissable — proving content similarity
  never substitutes for real identity (AD-W7-17).
- **Distinct-account test (explicit, this correction pass — the concrete
  case Control Tower flagged):** proposing from two DTOs sharing the exact
  same `external_source_id` but under two **different** fake
  `account_namespace` values (simulating two Google accounts authorized
  through the same NOVA OAuth Desktop Client) creates **two distinct
  rows**, neither deduplicated against the other — proving OAuth-client
  identity is never conflated with account identity. Conversely, the same
  `account_namespace` and the same `external_source_id` together dedupe
  correctly, per the stable-identity test above.
- **`account_namespace` sourcing test:** `WorkspaceBridgeService` calls
  `GoogleAuthenticator.get_account_namespace()` and never reads or
  references `client_id_hash` anywhere — verified by a structural test
  asserting `app/workspace_bridge/*.py` contains no reference to
  `client_id_hash`.
- `commit()` — each `target_type` calling the correct downstream service
  method exactly once; invalid `target_type` fails closed; committing an
  already-terminal row raises a typed stale-state error; a downstream
  failure leaves the candidate row `candidate`, not partially committed.
- `dismiss()` — valid, already-terminal rejected.
- Structural test: `app/workspace_bridge/*.py` contains no raw SQL against
  `control_tower_work_items`, `knowledge_items`, or `knowledge_sources`.
- Additive schema migration test (new table) + the one-value `CHECK`
  widening on `KnowledgeSource.source_type`.
- Telegram commands — valid, missing/invalid ref ID, unauthorized user.
- Full existing regression suite (759 passing) passes unmodified.

## 11. Acceptance criteria

1. Proposing a candidate from the exact same real email/event/file twice
   returns the same row, never a duplicate.
2. Proposing from two distinct real objects that share identical or
   near-identical text content always creates two distinct, independently
   reviewable rows — verified by test, not merely by design intent.
3. Proposing from the same real `external_source_id` under two different
   `account_namespace` values always creates two distinct rows — verified
   by test, not merely by design intent.
4. `app/workspace_bridge/**` never references `client_id_hash`;
   `account_namespace` is sourced exclusively from
   `GoogleAuthenticator.get_account_namespace()`.
5. `commit()` creates exactly one real `WorkItem`/`Decision`/`KnowledgeItem`
   through its existing canonical service, and only on explicit user
   confirmation.
6. No raw write to `control_tower_work_items`/`knowledge_items`/
   `knowledge_sources` exists anywhere in `app/workspace_bridge/`.
7. Zero edits to `app/control_tower/**`; the sole edit inside
   `app/knowledge/**` is the one-value additive `CHECK` widening (§8).
8. Full existing regression suite (759 passing) passes unmodified.

## 12. Integration contract

Stage 3 (parallel with 8C — no code dependency on either), beginning only
after Stage 2 Integration Gate G2 passes. Hard dependency on 8A/8B
(Stages 1–2) for the DTOs it proposes candidates from, and on 8A's
`get_account_namespace()` specifically for `account_namespace`. 8D's own
merge, alongside 8C's, and the subsequent G3 wiring pass, are gated by
Stage 3 Integration Gate G3 (`docs/WAVE_7_SHARED_CONTRACTS.md` §12) before
Stage 4 may begin.

## 13. Explicit prohibited edits

- No edits to `app/control_tower/**`.
- No edits to `app/knowledge/**` beyond the single additive `source_type`
  `CHECK`-constraint value (§8) — every other file in that package
  untouched.
- No edits to `app/drafting/**` (8C), `app/intake/**` (8F),
  `app/workspace_actions/**` (8E).
- No edit to `app/main.py`, and no edit to `build_application()`'s function
  signature — owned by the G3 integration branch (§6, §7).
- No use of `content_fingerprint` (or any content-derived value) as a
  uniqueness or identity constraint.
- No use of `client_id_hash` as `account_namespace`, or as any part of
  `WorkspaceSourceRef`'s identity.

## 14. Known risks / technical debt

- Deterministic candidate-extraction heuristics are intentionally simple
  for v1 — false negatives (missed candidates) are the safe failure
  direction; false positives are mitigated by the mandatory explicit-commit
  step, not by extraction accuracy.
- The one-value `KnowledgeSource.source_type` widening is a cross-package
  edit by a sprint that otherwise owns none of `app/knowledge/**` — flagged
  for extra scrutiny at review time.
- `account_namespace` reflects the one real, currently-configured Google
  account today (NOVA supports one token file at a time) — the column and
  its correct account-derived value both exist so a future multi-account
  NOVA needs no schema change and no silent provenance collision, not
  because Wave 7 itself supports multiple simultaneous accounts.
- The exact library mechanism `GoogleAuthenticator.get_account_namespace()`
  uses internally is implementation-time-verified (`docs/SPRINT_8A.md` §14,
  AD-W7-17) — 8D depends only on the method's frozen semantic contract
  (stable, distinct per account, opaque), not on any particular Google
  library call, so this is not a risk to 8D's own design.
- `commit()`'s same-transaction guarantee spans two services using the same
  underlying SQLite file via independent `MemoryDatabase.connection()`
  calls; `commit()` mitigates the resulting eventual-consistency edge case
  by writing its own `status='committed'` only after the downstream call
  returns successfully, so the worst-case failure mode is a candidate stuck
  at `candidate` status (safe, re-committable), never a falsely-committed
  reference — matching 7E's own documented SQLite multi-connection caveat.
