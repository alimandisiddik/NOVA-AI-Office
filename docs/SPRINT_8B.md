# Sprint 8B — Inbox & Calendar Intelligence

## Status: **FROZEN** — architecture decisions applied.

See `docs/WAVE_7_SHARED_CONTRACTS.md` §2, §4 (AD-W7-12, AD-W7-15), §7 for
the cross-sprint contract this spec implements.

## 0. FACT vs. INFERENCE vs. RECOMMENDATION — frozen intelligence policy

Every value 8B produces is tagged as exactly one of three kinds, and this
tag is never dropped before rendering:

- **FACT** — read verbatim from Gmail/Calendar via 8A (e.g. "received
  yesterday, unread, sender `sender_a1b2c3`").
- **INFERENCE** — a deterministic (or, optionally, LLM-assisted) judgment
  derived from facts (e.g. "likely needs a response").
- **RECOMMENDATION** — a suggested next step (e.g. "prepare a reply").

NOVA never presents an INFERENCE or RECOMMENDATION as if it were a FACT —
every rendered inbox/agenda line carries its facts and its inference/
recommendation as visibly distinct fields (§8's `PrioritizedMessage.reason`
is always phrased as an inference, never asserted as certain). If LLM
ranking/classification is used, it can only ever populate the INFERENCE/
RECOMMENDATION fields — never silently rewrite a FACT field — and remains
optional, bounded, and metadata-level-auditable, with no email silently
converted into a `WorkItem` (that promotion is 8D's exclusive, always-
explicit capability).

## 1. Objective

Turn 8A's read-only Gmail/Calendar connectivity into executive situational
awareness: what needs attention today, without ever mutating anything.

## 2. User-visible usable capability

A Telegram command shows a prioritized inbox view (messages likely needing
reply/follow-up) and today's/tomorrow's meetings with preparation signals
(conflicts, missing agenda-relevant context), composed entirely from 8A's
existing read services.

## 3. Scope

- New `app/workspace_intel/` package: `models.py`, `service.py`.
- `WorkspaceIntelService.prioritized_inbox(limit: int = 20) ->
  PrioritizedInboxView` — calls `GmailService.search_messages()` (8A),
  scores each `MessageSummary` deterministically (unread + recent + no
  prior reply signal in subject like "Re:" absence + sender not a known
  bulk/no-reply pattern → higher priority), returns a bounded, sorted view.
  No LLM in this path by default.
- `WorkspaceIntelService.upcoming_agenda(days: int = 2) -> AgendaView` —
  calls `CalendarService.list_today()`/`.list_week()`/`.detect_conflicts()`
  (8A/existing), surfaces today's and tomorrow's meetings, flags conflicts
  already detected by the existing `detect_conflicts()` method (reused, not
  reimplemented), and flags meetings NOVA has no prepared brief for (a
  meeting-preparation *signal*, not an automatic action).
  `CalendarService.build_meeting_brief()` (existing) is reused as-is for
  each flagged meeting's summary shape.
- `WorkspaceIntelService.deadlines_and_invitations(...)` — a read
  composition over Calendar (invitations = events with `attendee_count > 0`
  where the user is not the organizer alias) and, optionally, 8D's
  candidate `WorkspaceSourceRef` deadlines once 8D lands (optional
  constructor parameter, `None`-safe — 8B does not hard-block on 8D, which
  in fact merges *after* 8B per the frozen stage order; this is a forward-
  compatible optional hook, not an active Stage 2 dependency).
- Optional LLM enrichment: a `summarizer: Callable[[str], str] | None = None`
  constructor parameter that, if present, may produce a one-line natural-
  language gloss on top of the deterministic view; absent, every view still
  renders correctly from deterministic fields alone (AD-W7-12).
- New read-only Telegram commands: `/inbox`, `/agenda`.

## 4. Out of scope

- Sending, replying, archiving, labeling, or deleting any Gmail message.
- Creating, updating, or deleting any Calendar event.
- Any LLM call in the default/required code path.
- Automatic `WorkItem` creation from an inbox/agenda item — that is 8D's
  exclusive capability, invoked only through 8D's own explicit
  candidate-then-confirm flow, never implicitly from an `/inbox` read.
- A second, persisted "inbox" database — every view is computed fresh from
  `GmailService`/`CalendarService` on each call; nothing about message or
  event state is cached or duplicated locally.
- Background polling of Gmail/Calendar — `/inbox`/`/agenda` are pull-only,
  triggered by an explicit command (AD-W7-12).

## 5. Existing architecture reused

- `app.google_workspace.gmail.service.GmailService` (8A) — read-only calls
  only.
- `app.google_workspace.calendar.service.CalendarService` — `list_today()`,
  `list_week()`, `detect_conflicts()`, `build_meeting_brief()`, all
  pre-existing (Sprint 5D), reused unchanged.
- 7C's `ExecutiveBriefService`'s composition-only pattern (calls existing
  public read methods, returns a plain frozen-dataclass DTO, no
  persistence) — `WorkspaceIntelService` follows the identical shape rather
  than inventing a different composition style.

## 6. Owned files/modules

- `app/workspace_intel/{models,service}.py` — new, 8B-exclusive. No
  `schema.py` — no persistence, matching 7C's `app/brief/` precedent
  exactly.
- `app/telegram_bot.py` — one additive block (`/inbox`, `/agenda`); one
  additive `build_application()` parameter,
  `workspace_intel: WorkspaceIntelService | None = None`, appended after
  Stage 1's three (8A/8F/8G already landed by the time Stage 2 starts —
  §7 of the shared contract).
- `app/main.py` — one additive construction block.
- `tests/test_workspace_intel_*.py` — new, 8B-exclusive.

## 7. Shared dependencies

- `app/google_workspace/**` — read only, existing public methods (8A).
- `app/workspace_bridge/**` (8D) — optional, read only, once merged
  (`None`-safe; 8D actually merges after 8B, so this dependency is inert at
  Stage 2 and only becomes live once Stage 3 lands — documented as a
  forward hook, not a blocking dependency).
- `app/main.py`, `app/telegram_bot.py` — shared, ordered append.

## 8. Data/contracts

No new persisted table. Plain frozen dataclasses (`models.py`):

```python
@dataclass(frozen=True)
class PrioritizedMessage:
    message: MessageSummary   # FACT — from app.google_workspace.gmail.dtos, verbatim
    priority_score: int        # INFERENCE — deterministic, 0-100
    reason: str                  # INFERENCE, phrased as a judgment, never asserted as fact
    recommendation: str          # RECOMMENDATION — e.g. "Prepare a reply"

@dataclass(frozen=True)
class PrioritizedInboxView:
    generated_at: str
    messages: tuple[PrioritizedMessage, ...]
    truncated: bool

@dataclass(frozen=True)
class FlaggedMeeting:
    brief: MeetingBrief        # from app.google_workspace.calendar.dtos
    has_conflict: bool
    missing_preparation: bool

@dataclass(frozen=True)
class AgendaView:
    generated_at: str
    today: tuple[FlaggedMeeting, ...]
    tomorrow: tuple[FlaggedMeeting, ...]
```

## 9. Security constraints

- Every field rendered is already privacy-scrubbed by 8A's DTOs
  (`sender_alias`, `organizer_alias`, hashed identities) — `WorkspaceIntel
  Service` adds no new raw-identity exposure.
- No write call anywhere in `app/workspace_intel/` — enforced by a
  structural grep test (mirrors 8A's own §9 discipline): no reference to
  any Gmail `send`/`modify`/`trash` or Calendar `insert`/`update`/`delete`
  method.
- Telegram rendering follows the existing sanitized-error convention.
- If a `summarizer` is injected, its output is still passed through
  `html.escape()`-equivalent sanitization before rendering (same discipline
  7E applies to dashboard HTML) since it is the one place free-form
  generated text could reach a Telegram message.

## 10. Tests

- `prioritized_inbox()` — deterministic scoring order, bounded/truncated
  result, empty-inbox case.
- `upcoming_agenda()` — conflict flag correctly sourced from
  `detect_conflicts()`, missing-preparation flag logic, empty-calendar case.
- **FACT/INFERENCE/RECOMMENDATION test (explicit):** asserts
  `PrioritizedMessage.message` fields are always byte-identical to the
  source `MessageSummary` (never altered by scoring), and that `reason`/
  `recommendation` text is drawn from a fixed, deterministic vocabulary
  never containing an assertion phrased as fact (e.g. never "this requires
  a response," always "likely requires a response" or equivalent hedged
  phrasing) — a structural/content test, not just a smoke test.
- Both — `summarizer=None` still produces a complete, correct view (no
  exception, no missing field).
- Structural test: no mutating Gmail/Calendar method referenced anywhere
  under `app/workspace_intel/`.
- `/inbox`/`/agenda` Telegram rendering — smoke test, unauthorized-user
  rejection.
- No network/LLM call occurs in the default code path — asserted the same
  way 7C asserts it (structural grep or a no-network-available test run).
- Full existing regression suite passes unmodified.

## 11. Acceptance criteria

1. `/inbox` and `/agenda` both render correctly using only 8A's existing
   read methods, with zero writes performed.
2. No LLM call occurs unless a `summarizer` is explicitly injected, and the
   view is complete and correct without one.
3. No new persisted table exists under `app/workspace_intel/`.
4. Zero edits to `app/google_workspace/**` (8B only calls its existing
   public methods).
5. Every `PrioritizedMessage`/`FlaggedMeeting` field is traceable to exactly
   one of FACT/INFERENCE/RECOMMENDATION, and no INFERENCE/RECOMMENDATION
   field is ever phrased or rendered as an assertion of fact (§0).
6. Full existing regression suite (759 passing) passes unmodified.

## 12. Integration contract

Stage 2 — merges after Stage 1 Integration Gate G1 passes (hard dependency:
`GmailService`/`CalendarService` must exist and be merged). Does not block
on 8F/8G (no relationship) or on 8D (optional, `None`-safe, and sequenced
later regardless). 8B's own merge is gated by Stage 2 Integration Gate G2
(`docs/WAVE_7_SHARED_CONTRACTS.md` §12) before Stage 3 may begin.

## 13. Explicit prohibited edits

- No edits to `app/google_workspace/**` (read-only import only).
- No edits to `app/intake/**`, `app/conversation/**`, `app/workspace_bridge/
  **`, `app/drafting/**`, `app/workspace_actions/**`.
- No new persisted table.
- No edit to another sprint's block in `app/main.py`/`app/telegram_bot.py`.

## 14. Known risks / technical debt

- Deterministic inbox scoring is intentionally simple (recency/unread/
  reply-signal heuristics) — a future sprint may refine it without a schema
  change, since nothing is persisted.
- The `workspace_bridge` optional hook (§7) is wired but inert until 8D
  merges; a reviewer at Stage 2 merge time should confirm it degrades
  correctly with `None` rather than assume it is exercised, since 8D's real
  behavior cannot be tested end-to-end until Stage 3.
