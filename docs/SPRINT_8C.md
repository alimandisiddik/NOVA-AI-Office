# Sprint 8C — Drafting & Document Operations

## Status: **FROZEN** — architecture decisions applied. Covers Gmail, Docs,
Sheets, and Slides preparation under one generalized `PreparedWorkspaceAction`
concept. Revised per Control Tower Freeze Review: **Google Keep is deferred
and out of active Wave 7 scope** — this sprint has no Keep content type,
Keep method, or Keep-related test. Further revised per a final Control Tower
correction: **8C no longer edits `app/main.py`/`build_application()`'s
signature directly** — that wiring is now owned by the Stage 3 (G3)
integration branch, the same isolation principle Stage 1 already applies.

See `docs/WAVE_7_SHARED_CONTRACTS.md` §3.7, §3.8, §4 (AD-W7-05, AD-W7-10,
AD-W7-14), §5a, §11 for the cross-sprint contract this spec implements.
**AD-W7-05 and §3.8 are the load-bearing decisions for this entire sprint —
read them before implementing.**

## 1. Objective

Prepare useful Workspace artifacts — draft email replies, Docs memos, Sheets
change-sets, Slides outlines — with **zero external side effects**. Nothing
8C produces ever reaches Google's servers.

## 2. User-visible usable capability

The user can ask NOVA to prepare a draft reply to an inbox item (8B), a
standalone memo, a spreadsheet change, or a slide-deck outline; NOVA
produces bounded, reviewable content stored locally, which the user can
review, request revisions to, or (once 8E lands) explicitly approve for real
execution on Google's side. 8C alone never creates or changes anything on
Google's side.

## 3. Scope

- New `app/drafting/` package: `models.py`, `schema.py`, `repository.py`,
  `service.py`.
- `PreparedWorkspaceAction` per §8 — a locally-persisted, NOVA-only record,
  generalized across all four active content types.
- `DraftingService`:
  - `prepare_gmail_reply(source_message_id, instructions, actor) ->
    PreparedWorkspaceAction` (`content_type='gmail_reply'`).
  - `prepare_gmail_new(instructions, actor) -> PreparedWorkspaceAction`
    (`content_type='gmail_new'`).
  - `prepare_docs_memo(title, instructions, actor) ->
    PreparedWorkspaceAction` (`content_type='docs_memo'`).
  - `prepare_sheets_change(source_file_id, a1_range, instructions, actor) ->
    PreparedWorkspaceAction` (`content_type='sheets_change'`;
    `body_payload` holds a JSON-serialized `{a1_range, values}` structure,
    never a plain-text body).
  - `prepare_slides_outline(title, instructions, actor) ->
    PreparedWorkspaceAction` (`content_type='slides_outline'`;
    `body_payload` holds a JSON-serialized ordered list of per-slide text).
  - `revise(action_id, instructions, actor) -> PreparedWorkspaceAction` —
    creates a new row referencing the prior one (`supersedes_id`), never
    mutates history in place, mirroring 7D's `reassign()` precedent.
  - `mark_ready_for_action(action_id, actor) -> PreparedWorkspaceAction` —
    transitions `prepared -> ready_for_action`, the **only** state 8E's
    `WorkspaceActionService` is allowed to read (§7) — the explicit,
    never-implicit handoff point between "locally prepared" and "eligible
    to become a real external write."
  - `get_ready_action(action_id) -> PreparedWorkspaceAction | None` — the
    one narrow read method 8E is allowed to call (§7); returns `None`
    unless `status == 'ready_for_action'`.
- New read-only Telegram commands: `/draftreply <message_id> |
  <instructions>`, `/draftmemo <title> | <instructions>`,
  `/draftsheet <file_id> | <a1_range> | <instructions>`,
  `/draftslides <title> | <instructions>`, `/drafts`, `/draft <id>`.

## 4. Out of scope

- **Any call to a Gmail, Docs, Sheets, or Slides write endpoint — including
  Gmail draft creation.** Per AD-W7-05, any real Google-side write belongs
  exclusively to 8E, Stage 4.
- **Google Keep, in any form.** Per Control Tower directive
  (`docs/WAVE_7_SHARED_CONTRACTS.md` §6, AD-W7-14), Keep is out of active
  Wave 7 scope entirely; 8C has no `keep_note` content type, no Keep
  preparation method, and no Keep-related test. This is a hard exclusion,
  not a deferred detail.
- Sending, sharing, or publishing anything.
- Automatic drafting triggered without an explicit user request.
- Rich text/HTML formatting, embedded images, or attachment handling — v1 is
  plain text (or, for Sheets/Slides, structured plain values) only.

## 5. Existing architecture reused

- `app.google_workspace.{gmail,docs,sheets,slides}.dtos` types — referenced
  by ID only (`source_message_id`, `source_file_id`); 8C never re-imports
  the corresponding `*Service` classes to independently re-fetch content
  beyond what a caller (e.g. a Telegram handler already holding a DTO)
  already passed in — avoids a second, independent Google read path.
- `app.security.SENSITIVE_CONTENT_PATTERN` — applied to `instructions`,
  `body_text`, and `body_payload`'s serialized text before persistence.
- 7D's "new row on revise, never mutate history" pattern (`reassign()`).
- Optional LLM injection pattern (7C's `agent_assignments=None`, 8B's
  `summarizer=None`) — reused for drafting's own optional generator.

## 6. Owned files/modules

- `app/drafting/{models,schema,repository,service}.py` — new, 8C-exclusive.
- `app/drafting/telegram.py` — new, 8C-exclusive: independently
  unit-testable handler functions for the five new commands, each reading
  `context.bot_data.get("drafting")` and degrading to a clear "unavailable"
  reply if absent (AD-W7-10, generalized to Stage 3 this pass).
- `tests/test_drafting_*.py` — new, 8C-exclusive.

**Not owned by 8C (see §7 and `docs/WAVE_7_SHARED_CONTRACTS.md` §11):**
`app/main.py`'s `DraftingService` construction, and the `drafting`
parameter on `build_application()`'s signature — both owned by the **G3
integration branch**, not by 8C's own branch. This corrects the prior
draft, which allowed 8C to append its own `build_application()` parameter
directly on the (rejected) reasoning that Stage 3 carried no parallel-
signature collision — Control Tower determined 8C and 8D are still two
genuinely parallel branches and would face the same collision Stage 1
already had to solve.

## 7. Shared-file rules

- `app/google_workspace/**` — type-reference only (`MessageSummary`,
  `DocumentContent`, `RangeValues`, `PresentationContent`), no service call.
- `app/workspace_intel/**` (8B) — optional, read only (a `/draftreply` may
  originate from an `/inbox` item); no hard dependency.
- `app/workspace_actions/**` (8E) — **inverted dependency, same discipline
  as 7A/7D (AD-W6-01) and 8D's equivalent (§3.8 of the shared contract):**
  8E reads `PreparedWorkspaceAction` rows with `status='ready_for_action'`
  through `get_ready_action()`; 8C never imports `app/workspace_actions/`.
- **Shared-file rules (revised per AD-W7-10, generalized this pass):** 8C's
  branch **does not edit** `app/main.py` or add a parameter to
  `build_application()`'s signature — `DraftingService` construction and
  the `drafting`/`bot_data` wiring are added once, by the **G3 integration
  branch**, alongside 8D's equivalent. 8C's branch **may optionally** add
  its own single, isolated `application.add_handler(...)` line per command
  for branch-level end-to-end testability; if it does not, G3 adds them.
  This is the same mechanism AD-W7-10 already establishes for Stage 1
  (8A/8F/8G → G1), now applied identically to Stage 3.

## 8. Data/contracts

New additive table, owned entirely by `app/drafting/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS prepared_workspace_actions (
    id                 INTEGER PRIMARY KEY,
    content_type       TEXT NOT NULL CHECK (content_type IN
                          ('gmail_reply','gmail_new','docs_memo',
                           'sheets_change','slides_outline')),
    source_ref         TEXT,
    title              TEXT,
    body_text          TEXT,
    body_payload       TEXT,
    status             TEXT NOT NULL DEFAULT 'prepared'
                       CHECK (status IN ('prepared','ready_for_action','superseded')),
    supersedes_id      INTEGER REFERENCES prepared_workspace_actions(id),
    created_by         TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prepared_action_status ON prepared_workspace_actions(status);
CREATE TABLE IF NOT EXISTS drafting_audit_log (
    id         INTEGER PRIMARY KEY,
    action_id  INTEGER NOT NULL REFERENCES prepared_workspace_actions(id) ON DELETE CASCADE,
    event      TEXT NOT NULL,
    actor      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
```

Exactly one of `body_text` (for `gmail_reply`/`gmail_new`/`docs_memo`) or
`body_payload` (a bounded JSON string, for `sheets_change`/`slides_outline`)
is populated, enforced by `DraftingService`'s own validation per
`content_type` — a service-layer rule, not a multi-column SQLite `CHECK`,
matching how the rest of this repository validates conditional field
combinations (e.g. `DispatchService._validate_request()`). `source_ref` is
a loose, opaque reference (Gmail message ID or Drive/Docs/Sheets/Slides
file ID) — never a raw URL, never duplicated content beyond what the draft
body legitimately needs to say.

`PreparedWorkspaceActionType` (closed vocabulary, `models.py`) — exactly the
five values in the `CHECK` constraint above. **No `keep_note` value exists.**

## 9. Security constraints

- `instructions`, `body_text`, and `body_payload` are all screened with
  `SENSITIVE_CONTENT_PATTERN` before persistence, and all length-bounded.
- **Structural test (load-bearing for this sprint):** no file under
  `app/drafting/` imports `googleapiclient` or references any write method
  of any Google API (`drafts().create`, `messages().send`,
  `documents().batchUpdate`, `spreadsheets().values().update`,
  `presentations().batchUpdate`) — the single test that proves AD-W7-05's
  boundary holds in code, not just in the document.
- `sheets_change`'s `a1_range` is validated with the same strict A1-notation
  pattern 8A's `SheetsService.get_range()` already uses (reused, not
  reinvented).
- Telegram errors are sanitized, matching every existing domain's
  convention.

## 10. Tests

- `prepare_*()` (all four) — valid input per `content_type`, sensitive-
  content rejection, length-bound rejection, malformed `a1_range` rejection
  for `prepare_sheets_change()`.
- `revise()` — creates a new row, marks the prior row `superseded`,
  preserves history (old row still readable, never deleted).
- `mark_ready_for_action()` — valid transition; rejects an already-
  `ready_for_action`/`superseded` row (no double-transition).
- `get_ready_action()` — returns `None` for a `prepared`-only or
  `superseded` action, returns the row only when `ready_for_action`.
- **Structural test:** zero references to any Google write endpoint or
  `googleapiclient` import anywhere under `app/drafting/`.
- **Structural test:** the string `keep`/`Keep` (case-insensitive) does not
  appear as a `content_type` value or method name anywhere under
  `app/drafting/`.
- Telegram commands — valid, missing-field usage messages, not-found action
  ID.
- Full existing regression suite (759 passing) passes unmodified.

## 11. Acceptance criteria

1. Every `PreparedWorkspaceAction`, across all four active content types,
   is created and revised entirely locally; no test or code path calls a
   Google API — verified by the structural import/method-reference test,
   not just by inspection.
2. `revise()` never mutates a prior action's row — full history is
   readable via `supersedes_id` chains.
3. `mark_ready_for_action()` is the only, explicit state transition that
   makes an action visible to 8E's `get_ready_action()` read path — a
   `prepared`-status row is verifiably invisible to that method.
4. Zero write scope requested, zero write-capable dependency imported,
   anywhere in `app/drafting/`.
5. `content_type`'s closed vocabulary contains exactly five values, none of
   them Keep-related.
6. 8C's branch diff contains no edit to `app/main.py` and no edit to
   `build_application()`'s function signature — verified directly against
   the branch diff, not merely asserted.
7. Full existing regression suite (759 passing) passes unmodified.

## 12. Integration contract

Stage 3 (parallel with 8D — no code dependency on either), beginning only
after Stage 2 Integration Gate G2 passes. Hard dependency on 8A (type
reference, merged Stage 1); soft/optional dependency on 8B (Stage 2). 8C's
own merge, alongside 8D's, and the subsequent G3 wiring pass, are gated by
Stage 3 Integration Gate G3 (`docs/WAVE_7_SHARED_CONTRACTS.md` §12) before
Stage 4 may begin.

## 13. Explicit prohibited edits

- No edits to `app/google_workspace/**` beyond importing existing DTOs by
  type.
- No edits to `app/workspace_bridge/**` (8D), `app/intake/**` (8F),
  `app/conversation/**` (8G), `app/workspace_actions/**` (8E).
- No new OAuth scope requested anywhere in this sprint's diff.
- No edit to `app/main.py`, and no edit to `build_application()`'s function
  signature — owned by the G3 integration branch (§6, §7).
- No Keep-related content type, method, or command.

## 14. Known risks / technical debt

- Content generation is deterministic-template-based by default unless an
  LLM generator is injected — quality is intentionally basic in v1; richer
  generation is additive, non-breaking future work.
- `source_ref`/`source_file_id` have no existence validation against a live
  Google account (8C never calls Google) — a draft can reference an ID that
  later disappears; acceptable because 8C's artifact is informational until
  8E acts on it, at which point 8E's own execution surfaces a real, safe
  failure if the source no longer exists.
- `body_payload`'s JSON shape for `sheets_change`/`slides_outline` is
  defined here at a conceptual level (`{a1_range, values}` / ordered
  per-slide text list); the implementer should pin an exact JSON Schema in
  code, not re-derive it ad hoc, to keep 8E's consuming code and 8C's
  producing code from drifting apart silently.
- If Google Keep is ever revisited (AD-W7-14's four conditions), 8C's
  `content_type` enum and its `body_text`/`body_payload` split are already
  shaped to accept a future `keep_note` addition without a structural
  redesign — noted for the future sprint that would do that work, not
  acted on now.
