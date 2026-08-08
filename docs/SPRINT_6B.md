# Sprint 6B — Dissertation Research & Evidence Workflow

## Status
Implemented — independently reviewed, corrected, and given a final
corrective pass for evidence confidence. All Sprint 6B acceptance criteria
are complete. **READY FOR STAGING.**

## Overview
This sprint was scoped to extend the Sprint 6A Dissertation Workspace
foundation into a usable research evidence workflow: sources, evidence,
research gaps, and a Telegram surface to work with them.

An independent review of the first pass found that the data model,
repository, and service-layer behavior this sprint's spec calls for
(SOURCE, EVIDENCE, and RESEARCH GAP support, multi-chapter source mapping,
sensitive-content protection, additive migrations) were already delivered
in Sprint 6A's merged `feat: add full dissertation workspace` commit
(`500664e`) — see `app/dissertation/{schema,models,repository,service}.py`
and `tests/test_dissertation_*.py`, all last touched by that commit, none
by this sprint. Prior to review, this sprint's branch contained **no
application-code changes** — only documentation (this file and
`CURRENT_SPRINT.md`) asserting the work as newly done, plus a doc-update
script (`update_docs.py`) whose regex over-matched `## Wave 5` headings and
duplicated the Sprint 6B summary into `CURRENT_SPRINT.md` four times. Both
defects have been corrected as part of this review.

The one genuinely missing piece against the spec was write access: the
`/dissertation` Telegram namespace was entirely read-only, with no way to
add a source or add evidence, despite the spec explicitly requiring both.
That capability has been implemented in this review pass — see below.

A second, final corrective pass addressed one further spec requirement:
the accepted Sprint 6B specification requires evidence **confidence** and
validation. The prior review pass did not implement this — it is not
covered by Sprint 6A's schema and was not added by the write-command work
above. It has now been implemented as a minimal, additive,
backward-compatible capability rather than being marked not applicable.
See "Evidence Confidence (this pass)" below.

## Requirements Satisfied

1. **Preserve Sprint 6A Behavior**: No existing tables or logic were
   modified destructively. All changes (this sprint's Telegram write
   commands) are additive and reuse Sprint 6A's schema/validation as-is.
2. **SOURCE Support** *(delivered in Sprint 6A)*: `dissertation_sources`
   table with title, validated source type, citation, locator, and status;
   multi-chapter mapping via `dissertation_source_chapter_links`.
3. **EVIDENCE Support** *(delivered in Sprint 6A; confidence added this
   pass)*: `dissertation_evidence` table linking a source, chapter, and
   gap, with a bounded summary, locator detail, and a validated
   `confidence` level (`LOW`/`MEDIUM`/`HIGH`, default `MEDIUM`). Evidence
   strictly requires an existing source and, when supplied, an existing
   chapter/gap (`DissertationTargetNotFoundError` otherwise), and now also
   a confidence value drawn from the enum (`InvalidDissertationValueError`
   otherwise).
4. **RESEARCH GAP Support** *(delivered in Sprint 6A)*: `dissertation_gaps`
   table with type, status lifecycle (`open → in_progress/deferred →
   resolved`, `resolved` terminal), priority, and next-action tracking.
5. **Telegram Integration** *(read views: Sprint 6A; write commands: this
   review)*: `/dissertation` supports read views (`overview`, `chapter <n>`,
   `gaps`, `next`, `tasks`, `evidence <n>`, `sources [n]`, `decisions`) plus
   two new explicit, structured write commands:
   - `/dissertation addsource <chapter n|-> | title | source type | citation | locator`
   - `/dissertation addevidence <source id> | <chapter n|-> | summary | locator | confidence`

   The trailing `confidence` field on `addevidence` is optional
   (`LOW`/`MEDIUM`/`HIGH`, case-insensitive; documented default `MEDIUM`
   when omitted — added this pass).

   Both use the same pipe-delimited structured syntax already established
   by `/task`, `/note`, and `/project` — no conversational/free-text
   parsing. Both route through the existing, unmodified
   `DissertationService.create_source` / `create_evidence` validation
   (source-type allowlist, source/chapter existence, length bounds,
   `SENSITIVE_CONTENT_PATTERN` rejection) and the existing
   `_require_authorized_user` gate. The `sources` and `evidence <n>` read
   views were additively extended to print each record's id, so a user can
   reference a source when adding evidence.
6. **Security**: No URL fetching, PDF downloading, or external execution
   introduced. Locators/URLs remain metadata only. `SENSITIVE_CONTENT_PATTERN`
   validation applies to all free-text inputs, including the new write
   commands' title/citation/locator/summary fields. No secrets are logged;
   validation-error messages surfaced to the user are static, non-sensitive
   strings (e.g. "Invalid source type"), never raw input or stack traces.
7. **Out of Scope**: RAG, vector DBs, automated literature search, and
   automatic drafting remain strictly omitted. No provider/router, Night
   Shift, or Google Workspace code was touched.
8. **Tests**: 99 targeted dissertation tests pass
   (`tests/test_dissertation_*.py`, `tests/test_telegram_dissertation.py`):
   90 from the write-command review pass (12 new: success, chapter linking,
   omitted-chapter, invalid source type, sensitive-content rejection,
   unknown source/chapter, usage errors, and unauthorized-access rejection)
   plus 9 new this pass for evidence confidence (valid enum values,
   case-insensitive normalization, invalid-value rejection, persistence,
   the additive migration against a simulated pre-existing table, and
   backward-compatible defaulting for callers that omit confidence).
9. **Documentation**: Corrected this file and de-duplicated
   `CURRENT_SPRINT.md`; both updated again this pass for evidence
   confidence.

## Evidence Confidence (this pass)

The accepted Sprint 6B specification requires evidence confidence and
validation. This was not delivered by Sprint 6A's schema (`dissertation_evidence`
had no confidence column) and was not added by the prior review's write-command
pass. Implemented here as the smallest additive, backward-compatible capability:

- **Schema** (`app/dissertation/schema.py`): `dissertation_evidence` gains a
  `confidence TEXT NOT NULL DEFAULT 'MEDIUM' CHECK (confidence IN ('LOW',
  'MEDIUM', 'HIGH'))` column, added to `EVIDENCE_SCHEMA`'s
  `CREATE TABLE IF NOT EXISTS` for fresh databases, and via a new
  `EVIDENCE_CONFIDENCE_MIGRATION` (`ALTER TABLE ... ADD COLUMN`) guarded by a
  `PRAGMA table_info(dissertation_evidence)` check in `apply_schema()` for
  any database that already has the table without the column — the exact
  pattern already used for `version_state` and `current_focus`. No table is
  dropped or rewritten; existing evidence rows are left untouched and simply
  receive `MEDIUM` for the new column, so no row becomes invalid.
- **Model** (`app/dissertation/models.py`): `Evidence.confidence: str` added.
  Since rows are always mapped by column name (`Evidence(**dict(row))`), no
  existing construction site needed to change.
- **Repository** (`app/dissertation/repository.py`): `EVIDENCE_CONFIDENCE_LEVELS
  = frozenset({"LOW", "MEDIUM", "HIGH"})` and `DEFAULT_EVIDENCE_CONFIDENCE =
  "MEDIUM"`. `create_evidence(..., confidence: str = DEFAULT_EVIDENCE_CONFIDENCE)`
  normalizes the value (`.strip().upper()`) and raises
  `InvalidDissertationValueError("Invalid evidence confidence")` for anything
  outside the enum, mirroring the existing `gap_type`/`priority` validation
  style in `create_gap`.
- **Service** (`app/dissertation/service.py`): `create_evidence(...,
  confidence: str = "MEDIUM")` passes the value straight through to the
  repository, which is the single source of truth for validation and
  normalization.
- **Telegram** (`app/telegram_bot.py`): `/dissertation addevidence` accepts
  confidence as an optional trailing 5th pipe field —
  `<source id> | <chapter n|-> | <summary> | <locator> | <confidence>` —
  normalized case-insensitively before being passed to the service. Omitting
  it uses the documented `MEDIUM` default. An invalid value surfaces the
  same static `InvalidDissertationValueError` message already caught by the
  handler's existing error path — no new error handling added. The
  confirmation reply now echoes the stored confidence, e.g. `Evidence
  created: #3 for source #1 (confidence: HIGH)`.
- **Why a trailing positional field, not a named flag**: `addevidence`
  already uses strictly positional pipe fields with no named-parameter
  syntax (matching `/task`, `/note`, `/project`, and `addsource`). Adding
  confidence as the next positional field preserves that convention. The
  one consequence is that a caller who wants to set confidence without a
  locator must still supply a `locator` field (existing fields are
  positional and non-skippable, matching `addsource`'s locator handling) —
  documented here as the accepted, practical trade-off rather than adding
  new parsing complexity for a single optional field.

## Architecture Decisions (AD)

* **AD-1** *(Sprint 6A)*: Standard `sqlite3` PRAGMA-guarded migrations for
  new tables, mirroring the `VERSION_STATE_MIGRATION` pattern.
* **AD-2** *(Sprint 6A)*: Task management continues to delegate to the
  pre-existing Workspace Memory/Control Tower rather than a new task table;
  Research Task Links bridge the two domains.
* **AD-3** *(Sprint 6A)*: Non-destructive `current_focus` column addition
  for `dissertation_chapters`.
* **AD-4** *(this review)*: Telegram writes are a thin, structured-syntax
  layer over the existing service/repository validation — no new
  parsing, schema, or authorization logic. Chapter linking at evidence
  time reuses the existing single-`chapter_id` field on
  `dissertation_evidence`; associating one piece of evidence with multiple
  chapters is out of scope for the Telegram surface (the underlying
  `dissertation_source_chapter_links` table already supports multi-chapter
  sources, independent of evidence linkage).
* **AD-5** *(final corrective pass)*: Evidence confidence is a single
  validated `TEXT` enum column with a `CHECK` constraint and a `NOT NULL
  DEFAULT`, not a separate lookup table — proportionate to a
  three-value, closed enum, and consistent with how `dissertation_gaps`
  models `priority`/`status` as `CHECK`-constrained text rather than
  foreign keys. Validation and normalization live once, in the repository,
  so the Telegram layer and any future caller share the same rules instead
  of duplicating an enum check.

## Test Results

- Targeted dissertation suite: 99 passed
  (`tests/test_dissertation_next_action.py`,
  `tests/test_dissertation_repository.py`, `tests/test_dissertation_schema.py`,
  `tests/test_dissertation_security.py`, `tests/test_dissertation_service.py`,
  `tests/test_telegram_dissertation.py`).
- Canonical full regression (`python -m pytest -ra --tb=short`), run at the
  end of this final corrective pass: **644 passed, 0 failed**. From this
  sprint's branch point (`main` @ `93fc11e`): 623 passed originally, 635
  passed after the write-command review pass (623 + 12), **644 passed**
  after this pass's 9 new evidence-confidence tests (635 + 9). No
  regressions at any step.
- On the accepted-baseline discrepancy: an "accepted baseline of 639
  passing tests" does not exist on `main` or on this branch's ancestry.
  `main` @ `93fc11e` (this branch's parent) passes 623. A **separate,
  unmerged sibling branch**, `fix/5g4-telegram-error-diagnostics` (commit
  `26f7f50`, built on the same `93fc11e` parent), independently adds 16
  tests (`tests/test_telegram_bot_error.py`) for **639** — but that
  branch's own status is recorded as "Implementation under review — ...
  not yet merged." No commit in this repository's history on the `main`
  lineage has ever had 639 passing tests; no tests were lost or deleted by
  Sprint 6B. Merging Sprint 5G.4 was out of scope for this review
  (unrelated Telegram-error-handler work, explicitly not to be touched
  here) and is left to its own review track.
