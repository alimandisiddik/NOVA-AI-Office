# Sprint 8G — Conversational Control & Contextual Confirmation

## Status: **FROZEN** — architecture decisions applied. UX requirement is
user-approved. Revised per Control Tower Freeze Review: shared
`build_application()` signature/`app/main.py` wiring is now owned by the G1
integration branch, not by this sprint's own feature branch — **the
`handle_text` body edit itself remains 8G-owned and unchanged**, since it
was already the one zero-collision exception to the shared-bootstrap
concern.

See `docs/WAVE_7_SHARED_CONTRACTS.md` §3.5, §4 (AD-W7-08, AD-W7-09,
AD-W7-10), §7, §11 for the cross-sprint contract this spec implements.

## 1. Objective

Let the user reply to a NOVA-presented choice with a bare number or a short
contextual word (`lanjut`, `oke`, `ya`, ...) when the choice is low-risk,
while making it structurally impossible for the same short reply to
authorize a high-risk action.

## 2. User-visible usable capability

When NOVA presents a numbered menu (e.g. "1. Continue 2. Review 3. Stop"),
the user can reply `1`/`2`/`3`, or — for low-risk menus only — a short
Indonesian/English contextual phrase, and NOVA resolves the intended choice.
For a high-risk menu (one whose options include commit/push/merge/send/
publish/delete/destructive/security-change/external-write), only an exact
numbered (or exact-label) reply resolves it; anything else, including every
contextual phrase, causes NOVA to re-ask rather than guess or refuse
silently.

## 3. Scope

- New `app/conversation/` package: `models.py`, `schema.py`, `repository.py`,
  `service.py`, `telegram.py`.
- `PendingInteraction` per §8.
- `ConversationService`:
  - `ask(chat_id, user_id, source_command, prompt_summary, choices:
    list[Choice], ttl_seconds: int = 600) -> PendingInteraction` — creates a
    new `open` row, atomically superseding any prior `open` row for the same
    `chat_id` (AD-W7-08's replay-protection requirement). `Choice` carries
    `index`, `label`, `risk_level` (per-choice, closed vocabulary — §6 of the
    shared contract), and an opaque `action_token` the caller uses to look
    up what to actually do on resolution (never a raw callback/eval string).
  - `try_resolve_pending(user_id, reply_text) -> Resolution | None` — the
    **only** entry point `app/telegram_bot.py:handle_text` calls (§11 of the
    shared contract). Returns `None` immediately (no pending interaction, or
    reply doesn't look like a resolution attempt) so `handle_text` falls
    through unchanged. Otherwise applies the resolution algorithm (§9) and
    returns a `Resolution` (`resolved` with the chosen `action_token`,
    `ambiguous` with a re-ask message, or `expired`/`wrong_user` with a
    safe message) — **never executes the chosen action itself**; the
    original caller (whatever created the `PendingInteraction`) is
    responsible for acting on `action_token`, via whatever mechanism it
    already uses (e.g. re-invoking its own handler). This keeps
    `ConversationService` a pure confirmation-state machine with zero
    knowledge of what "commit" or "send email" actually does.
  - `expire_stale()` — a bounded sweep (called opportunistically on `ask()`
    and `try_resolve_pending()`, not a background poller — AD-W7-12) that
    transitions any `open` row past its `expires_at` to `expired`.

## 4. Out of scope

- Actually executing any action — 8G is a confirmation-state machine only;
  every caller (8E, Git-safety flows, future callers) remains responsible
  for acting on a resolved `action_token` itself.
- Any change to existing Git safety rules or approval flows — `commit`/
  `push`/`merge` remain governed exactly as documented in
  `docs/WAVE_7_SHARED_CONTRACTS.md` §6 and this repository's own operating
  rules; 8G does not weaken, bypass, or reinterpret them.
- Multi-user disambiguation — NOVA's existing single-authorized-user model
  is unchanged; `PendingInteraction.user_id` exists for defense-in-depth
  and clarity, not multi-tenant support.
- Natural-language intent parsing beyond the fixed contextual-vocabulary
  list (§9) — this is not a general chatbot; unrecognized text is always
  `ambiguous`, never guessed.

## 5. Existing architecture reused

- `app.security.SENSITIVE_CONTENT_PATTERN` — applied to `prompt_summary` and
  any free-text `Choice.label` before persistence.
- `app.security.is_authorized_user()` — `try_resolve_pending()` only
  considers a reply from the same `user_id` the `PendingInteraction` was
  created for; any other caller is a structural impossibility today (single
  authorized user) but the check exists explicitly, matching
  `ApprovalService.approve()`'s existing `approving_user_id !=
  self.authorized_user_id` defense-in-depth pattern.
- Compare-and-swap transition discipline (`BEGIN IMMEDIATE` + expected-state
  check) — reused from `DispatchService`/`AgentAssignmentService`'s existing
  pattern, applied to `PendingInteraction.status` transitions.

## 6. Owned files/modules

- `app/conversation/{models,schema,repository,service,telegram}.py` — new,
  8G-exclusive.
- `app/telegram_bot.py` — exactly one edit to `handle_text`'s body (the
  three-line check in §11 of the shared contract, inserted before
  `parse_workspace_intent()` is called). **This edit remains 8G's own,
  merged directly by 8G's branch — it is the one Stage 1 shared-file touch
  that stays sprint-owned rather than deferred to G1, because it is
  self-contained and no other Stage 1 sprint touches `handle_text` at all
  (`docs/WAVE_7_SHARED_CONTRACTS.md` AD-W7-10, point 5). 8G is the only
  sprint permitted to edit `handle_text`'s body.**
- `tests/test_conversation_*.py` — new, 8G-exclusive.

**Not owned by 8G (see §7 and `docs/WAVE_7_SHARED_CONTRACTS.md` §11):**
`app/main.py`'s `ConversationService` construction, and the `conversation`
parameter on `build_application()`'s signature — both owned by the G1
integration branch, not by 8G's own branch.

## 7. Shared dependencies

- `app/memory/**` — none (uses `MemoryDatabase` directly for its own new
  table only, same as every other domain).
- `app/intake/**` (8F) — 8F is an optional *caller* of 8G's `ask()`/
  `try_resolve_pending()` surface; 8G has no dependency in the other
  direction and never imports `app/intake/`.
- `app/workspace_actions/**` (8E) — 8E is the primary intended caller of
  8G's HIGH-risk path (approval-style confirmations for external writes);
  8G has no dependency on 8E and never imports it. 8E is Stage 4; 8G ships
  in Stage 1 fully self-contained and independently testable via a fake
  caller.

**Shared-file rules (revised per AD-W7-10):** 8G's branch **does not edit**
`app/main.py` or add a parameter to `build_application()`'s signature —
`ConversationService` construction and the `conversation`/`bot_data` wiring
are added once, by the **G1 integration branch**, alongside 8A's and 8F's
equivalents. `handle_text`'s body edit (§6) is the sole exception, made
directly by 8G, and reads its service defensively via
`context.bot_data.get("conversation")` (§9's resolution algorithm is
unaffected by whether that key is populated yet or not — it simply returns
`no_pending`/falls through if `conversation` is `None`, exactly as it
already did when no interaction is open). 8G's branch may not add any other
edit to `app/telegram_bot.py` — it has no new command to register.

## 8. Data/contracts

New additive table, owned entirely by `app/conversation/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS conversation_pending_interactions (
    interaction_id      TEXT PRIMARY KEY,
    chat_id             TEXT NOT NULL,
    user_id             INTEGER NOT NULL,
    source_command      TEXT NOT NULL,
    prompt_summary      TEXT NOT NULL,
    choices_json        TEXT NOT NULL,
    max_risk_level       TEXT NOT NULL
                         CHECK (max_risk_level IN ('low','ambiguous_only','high')),
    status               TEXT NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open','resolved','expired','superseded')),
    resolved_choice_index INTEGER,
    resolved_at           TEXT,
    created_at            TEXT NOT NULL,
    expires_at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_open_per_chat
    ON conversation_pending_interactions(chat_id)
    WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_pending_status ON conversation_pending_interactions(status);
CREATE TABLE IF NOT EXISTS conversation_audit_log (
    id             INTEGER PRIMARY KEY,
    interaction_id TEXT NOT NULL REFERENCES conversation_pending_interactions(interaction_id) ON DELETE CASCADE,
    event          TEXT NOT NULL,
    actor          TEXT NOT NULL,
    detail         TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
```

The partial unique index (`WHERE status = 'open'`) enforces "at most one
open interaction per chat" at the database level, not just in service-layer
logic — a second `ask()` call for the same `chat_id` must transition the
existing open row to `superseded` inside the same transaction before
inserting the new row, or the insert itself fails closed.

`choices_json` stores a JSON array of `{index, label, risk_level,
action_token}`; `max_risk_level` is the pre-computed maximum across all
choices (derivable from `choices_json` but stored redundantly, deliberately,
so the resolution algorithm's risk gate — §9 — is a single indexed column
comparison, not a JSON parse-and-scan on every reply).

`models.py` (not persisted, service-layer DTOs):

```python
@dataclass(frozen=True)
class Choice:
    index: int
    label: str
    risk_level: str   # 'low' | 'high'
    action_token: str  # opaque; caller-defined, never executed by 8G itself

@dataclass(frozen=True)
class Resolution:
    outcome: str        # 'resolved' | 'ambiguous' | 'expired' | 'no_pending'
    action_token: str | None
    response_text: str
```

## 9. Security constraints — the resolution algorithm (frozen)

`try_resolve_pending(user_id, reply_text)`:

1. If no `open` `PendingInteraction` exists for this `user_id`'s chat, or the
   caller is not the authorized user: return `no_pending` (falls through to
   existing `handle_text` behavior unchanged).
2. `expire_stale()` runs first; if the row just expired, return `expired`
   with a "that request expired, please resend" message and no resolution.
3. **Exact numbered match** (`reply_text.strip()` equals a choice's
   `str(index)`, or exact case-insensitive match of a choice's full
   `label`): resolves **regardless of risk level** — this is the brief's own
   frozen example (numbered reply against a menu whose option text names the
   action is always explicit, §6/AD-W7-09). Transition to `resolved`,
   record `resolved_choice_index`, return the matching `action_token`.
4. **Contextual vocabulary match** (`lanjut`, `lanjutkan`, `teruskan`, `oke`,
   `ok`, `ya`, `setuju`, case-insensitive, exact-token match after
   whitespace/punctuation trim — not substring match, to avoid false
   positives inside longer sentences): resolves **only if
   `max_risk_level == 'low'`**. If `max_risk_level == 'high'`, this same
   input is treated as **ambiguous** (re-ask with an explicit reminder that
   a numbered reply is required) — it is never silently ignored and never
   treated as an implicit rejection, per the brief's "prefer conservative
   behavior" instruction resolved as "always re-prompt, never guess either
   direction."
5. Anything else: `ambiguous`, re-ask, no state change (row stays `open`,
   `expires_at` unchanged — does not reset the clock on every failed
   attempt, preventing indefinite keep-alive).
6. Every transition (`resolved`/`expired`/`superseded`) is compare-and-swap
   (`BEGIN IMMEDIATE`, expected `status='open'` check) — a second concurrent
   reply against an already-resolved interaction cannot double-resolve it
   (replay protection).

No token, credential, or full prompt payload is ever logged;
`conversation_audit_log.detail` records only the event name and (for
resolutions) the chosen index — never arbitrary user free text beyond what
`prompt_summary`/`Choice.label` already screened at creation time.

## 10. Tests

Per the shared contract's explicit test-strategy requirement for 8G:

- **Stale choice** — a reply arriving after `expires_at` returns `expired`,
  does not resolve, and the row's terminal state is `expired` not
  `resolved`.
- **Ambiguous "oke"** — a `high`-risk `max_risk_level` interaction receiving
  `"oke"` returns `ambiguous`, not `resolved`, and not silently dropped.
- **Wrong user** — a reply from a `user_id` other than the interaction's
  `user_id` (or a non-authorized user generally) returns `no_pending`/is
  rejected upstream by `_require_authorized_user`, never resolves.
- **Expired choice** — same as stale choice, plus a case where `ask()` is
  called again after expiry and the new interaction resolves independently
  (old row stays `expired`, never resurrected).
- **Replayed number** — resolving interaction A with `"2"`, then replaying
  `"2"` again after A is already `resolved`: second reply returns
  `no_pending` (A is terminal; no new open interaction exists), never
  re-executes A's `action_token`.
- **High-risk confirmation** — an exact numbered reply against a `high`-risk
  menu resolves; the exact label text reply also resolves; every contextual
  synonym is rejected as `ambiguous` for the same menu (parameterized over
  the full vocabulary list in §9.4).
- **Superseding** — calling `ask()` twice for the same chat transitions the
  first row to `superseded`; a reply naming the first (now-superseded)
  interaction's choice index does not resolve it.
- **Two simultaneous prompts** — two `ask()` calls issued back-to-back (no
  reply between them) for the same chat leave exactly one `open` row
  (database-enforced by the partial unique index), never two live
  interactions competing for the next reply.
- **Duplicate Telegram delivery** — the same inbound update (e.g. Telegram
  redelivering a message after a slow ack) processed twice against
  `try_resolve_pending()` resolves the interaction exactly once — the
  second call observes `status != 'open'` and returns `no_pending`, never
  re-triggering the `action_token` a second time.
- Additive schema migration test; partial-unique-index enforcement test
  (concurrent `ask()` attempts, only one open row survives).
- Full existing regression suite passes unmodified, including
  `tests/test_natural_language.py`/`test_telegram_memory.py`'s existing
  `handle_text` behavior when no `ConversationService` is injected or no
  interaction is open (byte-for-byte unchanged fallthrough).

## 11. Acceptance criteria

1. A low-risk numbered menu resolves via bare number or any listed
   contextual synonym.
2. A high-risk menu resolves only via exact numbered/label reply; every
   contextual synonym and every other free-text reply returns `ambiguous`
   with a re-ask, never a silent authorization and never a silent drop.
3. At most one `open` `PendingInteraction` exists per chat at any time,
   enforced at the database level.
4. A resolved or expired interaction can never be re-resolved by a later
   reply (replay protection verified by test).
5. `handle_text`'s existing behavior (workspace-intent parsing, static
   fallback) is unchanged when `ConversationService` is absent or no
   interaction is open.
6. Full existing regression suite passes unmodified.

## 12. Integration contract

Stage 1 (parallel with 8A, 8F — no code dependency on either, and, per the
revised AD-W7-10, no shared `build_application()`-signature dependency
either — 8G's branch never touches that signature; construction and
parameter wiring for `ConversationService` are owned by the G1 integration
branch, §7). 8G remains the only Stage 1 sprint that edits `handle_text`'s
body directly. 8G's merge into the Stage 1 integration branch, and the
subsequent G1 wiring pass, are gated by Stage 1 Integration Gate G1
(`docs/WAVE_7_SHARED_CONTRACTS.md` §12), alongside 8A and 8F, before Stage 2
may begin.

## 13. Explicit prohibited edits

- No edit to any other sprint's `app/telegram_bot.py` lines.
- No edit to `handle_text` beyond the three-line check at its top (§11 of
  the shared contract) — the rest of `handle_text`'s body, and every other
  command handler, is untouched.
- No edit to `app/main.py`, and no edit to `build_application()`'s function
  signature — owned by the G1 integration branch (§6, §7).
- No execution of any action inside `app/conversation/` — it returns
  `action_token`s, it never interprets or runs them.
- No change to Git safety rules, `ApprovalService`, or `DispatchService`.

## 14. Known risks / technical debt

- The fixed contextual-vocabulary list (Indonesian/English) is not
  user-extensible in v1 — adding a new synonym requires a code change, not
  a config change. Acceptable for v1 scope; flagged for a future sprint if
  the list needs to grow significantly.
- `try_resolve_pending()` returning `no_pending` for a reply that happens to
  be a bare digit with no interaction open means that digit falls through to
  `handle_text`'s normal parsing unchanged — this is intentional (a stray
  "2" typed with nothing pending should not do anything special), but is
  worth flagging so it isn't mistaken for a bug during testing.
- Because `ConversationService` never executes actions itself, every future
  caller (8E in particular) must correctly re-derive what `action_token`
  means on its own side — 8G guarantees *who* approved *which numbered
  choice*, not *what that choice does*. This boundary is deliberate (keeps
  8G free of domain knowledge) but places a correctness burden on callers
  that a future integration review should explicitly verify.
