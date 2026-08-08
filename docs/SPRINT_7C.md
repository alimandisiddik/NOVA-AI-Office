# Sprint 7C — Morning Executive Brief

## Status: **FROZEN** — final ChatGPT/Control Tower architecture decisions applied.

See `docs/WAVE_6_SHARED_CONTRACTS.md` §2.7 and AD-W6-05 for the contract
this spec implements.

## 1. Objective

Generate a deterministic executive morning brief from NOVA's own operational
state — no LLM in the critical path.

## 2. User-visible usable capability

A single Telegram command produces a structured brief: active priorities,
completed since last brief, waiting for decision, blockers, next actions —
composed entirely from already-canonical state (`WorkItem`, `Decision`,
`Approval`, the Night Shift snapshot, and, once merged, `AgentAssignment`).

## 3. Scope

- New `app/brief/` package: `models.py` (the `BriefItem` DTO — a plain
  dataclass, no persistence), `service.py`
  (`ExecutiveBriefService.generate_morning_brief()`).
- Composition only — calls existing public read methods:
  - `ControlTowerService.get_today_priorities()`
  - `ControlTowerService.list_approvals()`
  - `ControlTowerService.repository.list_work_items(["completed"])` (or an
    equivalent existing public read) filtered to "since last brief" by
    `updated_at`
  - `NightShiftService.get_latest_morning_brief()`
  - `NightShiftService.list_night_jobs()` (already used by
    `ControlTowerService.evening_shutdown()` — same read pattern)
  - Optional, if merged: `AgentAssignmentService`'s read surface (active
    assignments) and `KnowledgeService`'s read surface (recent items) —
    both optional constructor parameters, `None`-safe, same pattern as
    7A's optional `agent_assignments` injection.
- New Telegram command `/execbrief` (deliberately not reusing `/morning`'s
  name — see §14).

## 4. Out of scope

- Any LLM call to determine factual operational state. An LLM
  summarization/presentation layer over the deterministic `BriefItem` is an
  explicitly optional, later, presentation-only addition — not built here.
- Persisting a brief history/snapshot table (AD-W6-05; revisit only if a
  future sprint needs brief replay).
- Any write path — this sprint is 100% read-only composition.
- Editing `app/control_tower/service.py` or `app/nightshift/service.py` —
  7C calls their existing public methods only.

## 5. Existing architecture reused

- `app.control_tower.models.MorningBrief` / `ControlTowerService.morning_brief()`
  — the existing live-aggregation pattern this sprint follows exactly
  (read several domains' existing public methods, compose a DTO, don't
  persist). `ExecutiveBriefService` is a sibling to this, not a
  replacement — `ControlTowerService.morning_brief()` is untouched.
- `app.nightshift.models.MorningBrief` / `NightShiftService.get_latest_morning_brief()`
  — the persisted per-night snapshot, read as one input among several.
- `ControlTowerService.evening_shutdown()`'s exact pattern of reading
  `night_shift.list_night_jobs()` for cross-domain state — reused for
  consistency, not reimplemented differently.

## 6. Owned files/modules

- `app/brief/{models,service}.py` — new, 7C-exclusive. No `schema.py` — no
  persistence.
- `app/telegram_bot.py` — one additive block (`/execbrief`).
- `tests/test_executive_brief_*.py` — new, 7C-exclusive.

## 7. Shared dependencies

- `app/control_tower/**` — read only, existing public methods.
- `app/nightshift/**` — read only, existing public methods.
- `app/agent_assignment/**` — optional read only, once merged.
- `app/knowledge/**` — optional read only, once merged.
- `app/main.py`, `app/telegram_bot.py` — shared, ordered append.

## 8. Data/contracts

No schema. `BriefItem` is a plain frozen dataclass returned by
`generate_morning_brief()`:

```python
@dataclass(frozen=True)
class BriefItem:
    generated_at: str
    active_priorities: list[WorkItem]
    completed_since_last_brief: list[WorkItem]
    waiting_for_decision: list[Decision]
    blockers: list[WorkItem]          # items with unresolved dependency blockers
    approvals_pending: list[Approval]
    next_actions: dict[str, str]      # item_id -> next_action text (7A's next_action_for, if available)
    night_shift_summary: NightShiftMorningBrief | None
```

"Since last brief" uses the same Jakarta-calendar-day boundary
`ControlTowerService.evening_shutdown()` already uses
(`datetime.now(JAKARTA).date()`), not a new time-window concept.

## 9. Security constraints

- Read-only throughout — no write call anywhere in `app/brief/`.
- No raw content, no sensitive fields — every value already passed through
  its owning domain's existing screening before `ExecutiveBriefService`
  ever sees it.
- Telegram rendering follows the existing sanitized-error convention.

## 10. Tests

- `generate_morning_brief()` with a fully populated fixture DB — asserts
  every section is populated and internally consistent (e.g. every
  `waiting_for_decision` decision has `status == "pending"` or equivalent).
- `generate_morning_brief()` with an empty DB — every section is an empty
  list/`None`, no exception.
- `generate_morning_brief()` with `agent_assignments`/`knowledge` services
  both `None` — brief still generates correctly (7C does not hard-block on
  7B/7D).
- No LLM/network call is made anywhere in the code path — asserted via a
  test that runs with no network access available (or a structural grep
  test, mirroring Wave 3 §8's precedent).
- `/execbrief` Telegram rendering — smoke test only, formatting is not
  contract-critical.

## 11. Acceptance criteria

1. `/execbrief` produces a brief with all five required sections
   (active priorities, completed since last brief, waiting for decision,
   blockers, next actions) using only already-canonical NOVA state.
2. No LLM call occurs in the default code path.
3. No schema change, no edits to `app/control_tower/**` or
   `app/nightshift/**`.
4. Brief generation succeeds with 7B/7D unmerged (optional dependencies
   `None`).
5. Full existing regression suite passes unmodified.

## 12. Integration contract

Wave 2 — merges after 7A and 7D (reads their landed public methods for full
richness) but does not hard-require either at code level (§8, §11.4). See
`docs/WAVE_6_SHARED_CONTRACTS.md` §5 for the full order and gates.

## 13. Explicit prohibited edits

- No edits to `app/control_tower/**`, `app/nightshift/**`,
  `app/agent_assignment/**`, `app/knowledge/**`.
- No new persisted table.
- No edits to another sprint's block in `app/main.py`/`app/telegram_bot.py`.

## 14. Known risks / technical debt

- `/execbrief` is a new command name rather than replacing `/morning`'s
  output, deliberately — `/morning` (`ControlTowerService.morning_brief()`)
  is an established, tested Telegram surface; changing its output shape
  would be a behavior change to existing functionality outside this
  sprint's `app/control_tower/**` prohibition (§13). Whether `/morning`
  should eventually be redirected to `ExecutiveBriefService`'s richer
  output is a product decision for ChatGPT/Control Tower, and would be a
  distinct, explicitly-scoped follow-up sprint (it touches
  `app/control_tower/**`, which is 7A's exclusive territory this wave).
- "Since last brief" currently means "since local midnight," not "since the
  last time `/execbrief` was actually invoked" (there is no persisted brief
  history, AD-W6-05) — acceptable for v1, flagged so it isn't mistaken for
  a bug if the user calls `/execbrief` twice in one day and sees the same
  "completed since last brief" list both times.
