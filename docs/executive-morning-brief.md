# Executive Morning Brief

## Purpose

Sprint 7C provides a deterministic, on-demand executive view of NOVA's
canonical local state. `ExecutiveBriefService.generate_morning_brief()`
creates an in-memory `BriefItem`; it does not create a brief table, cache, or
audit entry.

## Canonical sources

`ExecutiveBriefService` calls only pre-existing `ControlTowerService` public
methods (`get_today_priorities()`, `list_approvals()`, `owner_for()`,
`next_action_for()`, `unresolved_blocker_count()`) plus its already-public
`repository` attribute (`list_work_items(...)`, `list_decisions_for_project(...)`)
exactly as `docs/SPRINT_7C.md` §3 names or permits ("or an equivalent existing
public read"). **`app/control_tower/**` is never edited by this package** —
that is 7A's exclusive territory per `docs/WAVE_6_SHARED_CONTRACTS.md` §4/§13.

- Control Tower supplies active priorities, work-item status, derived owner,
  derived next action, approvals, blockers, and completed work.
- "Waiting for decision" reuses the same candidate-synthesis rule
  `ControlTowerService.morning_brief()` already applies internally (work
  items in `clarification_needed`/`awaiting_approval` whose category is
  `policy`/`procurement`/`administrative`, rendered as `pending` decision
  candidates) — reimplemented locally in `app/brief/service.py` from the
  same public `repository.list_work_items(...)` read, not by calling or
  editing `control_tower.morning_brief()`.
- "Recent decisions" reads real registered `Decision` rows (`status ==
  "active"`) via the pre-existing `repository.list_decisions_for_project()`,
  scoped to the project IDs already present among the brief's active work —
  there is no pre-existing project-agnostic "list all decisions" read, and
  adding one would require editing `app/control_tower/**`, which this sprint
  must not do.
- Agent Assignment remains authoritative for active ownership. The brief uses
  `ControlTowerService.owner_for()`, which consumes only 7D's frozen
  `get_active_assignment_summary()` read interface.
- Knowledge Operations supplies linked `KnowledgeResult` values through
  `KnowledgeService.query()`. Each rendered item retains its source citation.
- Night Shift contributes only the existing latest persisted snapshot when a
  `NightShiftService` is supplied.

## Deterministic composition and bounds

- Active work follows `ControlTowerService.get_today_priorities()` ordering:
  priority score descending, then deadline, creation time, and item ID.
- Blockers use the same stable order after unresolved dependencies are
  identified by Control Tower.
- The brief shows at most 8 active items, 5 blockers, 5 approvals, 5 completed
  items, 4 knowledge results, and 4 decisions. Telegram renders through the
  existing 3,500-character bounded-message helper and truncates presentation
  text only, never canonical records.
- A supplied `now` value makes tests and callers deterministic; production
  generation records the Jakarta calendar date only.

## Telegram and safety

`/execbrief` is a new, distinct, authorization-gated command — per
`docs/SPRINT_7C.md` §2/§14, which deliberately chose a new command name
rather than changing `/morning`'s output, because `/morning`
(`ControlTowerService.morning_brief()`) is established, tested behavior
outside this sprint's `app/control_tower/**` prohibition (§13). `/morning`'s
handler and rendering are byte-for-byte unchanged by this sprint; it remains
registered exactly once and keeps producing its original `ControlTowerService
.morning_brief()`-based output, including the legacy Night Shift availability
line. `/execbrief` is a second, additive command, sharing `ExecutiveBriefService`
as its single composition layer (no duplicated business logic) and rendered
through the same `_executive_brief_lines()`/`_bounded_message()` helpers.

Generation uses local Python and SQLite reads only: no provider, LLM, network
call, dispatch, approval decision, assignment change, or execution occurs.
One caveat: `ControlTowerService.list_approvals()` — the exact method
`docs/SPRINT_7C.md` §3 names as a composition input — has always recorded its
own pre-existing `approval_aggregation` audit entry on every call (unchanged,
pre-dating this sprint); calling it from `/execbrief` therefore produces the
same audit entry `/morning`'s `morning_brief()` already produces today, not a
new side effect introduced by this sprint. No other read path in `app/brief/`
writes anything. Stored values are rendered only through the owning services'
safe read models; the brief never reads configuration or secret values.

## Deferred

LLM narrative enhancement, scheduled delivery, persistence/history, external
ingestion, embeddings/RAG, dashboard UI, and notification automation remain
out of scope.
