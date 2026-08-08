# Sprint 8F — `/wa` External Message Intake

## Status: **FROZEN** — architecture decisions applied. Product decision
(the `/wa` command and manual-forward model) is user-approved. Revised per
Control Tower Freeze Review: (1) shared Telegram/`app/main.py` bootstrap
wiring is owned by the G1 integration branch, not by this sprint's own
feature branch; (2) provenance identity redesigned around Telegram's own
delivery identity plus a scoped content fingerprint, replacing the earlier
content-hash-only dedup key.

See `docs/WAVE_7_SHARED_CONTRACTS.md` §3.5, §4 (AD-W7-06, AD-W7-07,
AD-W7-10, AD-W7-17), §5a, §7, §11 for the cross-sprint contract this spec
implements.

## 1. Objective

Let WhatsApp content reach NOVA's canonical workflow through manual
copy/forward into Telegram — no WhatsApp API, no WhatsApp Web automation,
no NOVA access to the user's WhatsApp account at all.

## 2. User-visible usable capability

The user copies a WhatsApp message and sends `/wa <pasted text>` to NOVA in
Telegram. NOVA classifies it (deterministically, with an optional LLM
enrichment layer that has a deterministic fallback), shows the user what it
found, and — only on the user's explicit confirmation — creates the
corresponding `Task`/`Note`/`KnowledgeItem`/follow-up through NOVA's
existing canonical services. Uncertain classification asks the user instead
of guessing. A repeated `/wa` of the same content — whether from an actual
duplicate Telegram delivery or an accidental double-paste — never silently
creates two canonical records.

## 2a. Provenance honesty policy (frozen)

`ExternalMessageIntake` provenance distinguishes three kinds of information,
never conflated:

- **KNOWN SOURCE FACT** — what Telegram/the manual copy actually gave NOVA:
  `raw_text`, `created_at` (receipt time in Telegram), `source_channel`,
  and, where available, `telegram_update_id` (§8).
- **USER-SUPPLIED DESCRIPTION** — anything the user explicitly typed about
  the message's origin — stored, but always labeled as user-supplied, never
  presented as something NOVA independently verified.
- **INFERENCE** — `classify()`'s output (`IntakeClassification`) — always
  labeled as an inference, never asserted as fact.

NOVA never invents WhatsApp metadata (sender number, delivery time, read
status, or a WhatsApp-native message ID) that the manual copy/forward did
not actually provide — there is no field for it, and no code path
fabricates one. This is the same discipline §8's identity redesign applies
structurally: NOVA does not pretend to have a WhatsApp-native identity it
was never given.

## 3. Scope

- New `app/intake/` package: `models.py`, `schema.py`, `repository.py`,
  `service.py`, `telegram.py` (the `/wa` handler function, written to read
  `context.bot_data.get("intake")` and degrade safely if absent — see §7).
- `ExternalMessageIntake` per §8 below.
- `IntakeService`:
  - `capture(source_channel: str, raw_text: str, actor: str,
    telegram_update_id: str | None = None) -> ExternalMessageIntake` —
    validates/screens `raw_text` (`SENSITIVE_CONTENT_PATTERN`, length
    bound), resolves identity per §8's two-tier rule, classifies it (§9),
    and persists (or returns an existing) row. Never calls any other
    domain's write method itself.
  - `classify(raw_text: str) -> IntakeClassification` — pure, deterministic
    function first, with an **optional** injected LLM classifier consulted
    only when the deterministic pass returns `uncertain` — `None`-safe. An
    uncertain result after both passes is surfaced to the user as a
    question, never auto-committed.
  - `confirm(intake_id, target_type, actor, **fields) ->
    ExternalMessageIntake` — the **only** method allowed to call
    `WorkspaceMemoryService`/`ControlTowerService`/`KnowledgeService`'s
    existing public write methods on behalf of an intake row. Never writes
    another domain's table directly.
  - `dismiss(intake_id, actor, reason) -> ExternalMessageIntake`.
- New Telegram command `/wa <text>`. V1 text intake only; the command takes
  the message text as its argument — no forwarded-message listener, no
  media handling.

## 4. Out of scope

- WhatsApp Business API, WhatsApp Cloud API, or any WhatsApp client library
  of any kind — permanently, not just for v1 (AD-W7-07).
- WhatsApp Web browser automation of any kind.
- Any reply sent back to WhatsApp.
- Media/image/voice-note intake (v1 is text-only).
- Automatic, unconfirmed `WorkItem`/`Note`/`KnowledgeItem` creation.
- A second copy of `WorkItem`/`Decision`/`KnowledgeItem`.
- Treating identical `raw_text` as proof of the same real-world message
  beyond the scoped, time-bounded window defined in §8 — content alone is
  never a permanent identity (AD-W7-17).
- **Constructing `IntakeService` inside `app/main.py`, adding a parameter to
  `build_application()`'s signature, or setting
  `application.bot_data["intake"]`.** These are owned by the G1 integration
  branch (§7).

## 5. Existing architecture reused

- `app.security.SENSITIVE_CONTENT_PATTERN` — applied to `raw_text` before
  any write.
- `app.memory.services.WorkspaceMemoryService` — existing public
  `create_task`/`create_note` methods, called only from `confirm()`.
- `app.control_tower.service.ControlTowerService` — existing public capture
  method, called only from `confirm()` when `target_type == "work_item"`.
- `app.knowledge.service.KnowledgeService` — existing public
  `create_source`/`create_item`, called only from `confirm()` when
  `target_type == "knowledge_item"`.
- 7B's `KnowledgeSource.origin_ref`/`origin_system` *pattern* (opaque
  reference, never raw payload duplicated elsewhere) — reused conceptually.

## 6. Owned files/modules

- `app/intake/{models,schema,repository,service,telegram}.py` — new,
  8F-exclusive.
- `tests/test_intake_*.py` — new, 8F-exclusive.

**Not owned by 8F (see §7 and `docs/WAVE_7_SHARED_CONTRACTS.md` §11):**
`app/main.py`, `build_application()`'s signature in `app/telegram_bot.py`.

## 7. Shared-file rules

Per the revised AD-W7-10 (`docs/WAVE_7_SHARED_CONTRACTS.md` §11):

- 8F's branch **does not edit** `app/main.py` or `build_application()`'s
  function signature. `IntakeService` construction and the `intake`
  parameter/`bot_data` wiring are added once, by the **G1 integration
  branch** — not by 8F.
- 8F's branch **may optionally** add a single, isolated
  `application.add_handler(CommandHandler("wa", wa_command))` line inside
  `build_application()`'s existing command-registration block, for
  branch-level end-to-end testability. This is the only
  `app/telegram_bot.py` edit 8F's own branch may make. If 8F's branch does
  not add this line, G1 adds it.
- `app/intake/telegram.py`'s `wa_command` reads `context.bot_data.get(
  "intake")` and replies with a clear "intake unavailable" message if
  absent — independently unit-testable with a hand-built fake `bot_data`,
  no dependency on `build_application()`.
- `app/memory/**`, `app/control_tower/**`, `app/knowledge/**` — write only
  through each domain's existing public methods, called exclusively from
  `IntakeService.confirm()`.
- `app/conversation/**` (8G) — **optional, read-only interaction**:
  `IntakeService.capture()` may hand its classification result to
  `ConversationService.ask(...)` (8G's public entry point) so the user's
  numbered/contextual reply flows through 8G's one confirmation mechanism.
  Optional constructor-injected dependency, `None`-safe — if absent, `/wa`
  falls back to an explicit `/waconfirm <id> <type>` command path. **8F does
  not import `app/conversation/` internals** — only its frozen `ask()`/
  `try_resolve_pending()` surface.
- `app/google_workspace/**` — no dependency, no edit.

## 8. Data models/interfaces — two-tier provenance identity (revised)

New additive table, owned entirely by `app/intake/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS external_message_intake (
    id                  INTEGER PRIMARY KEY,
    source_channel      TEXT NOT NULL DEFAULT 'whatsapp_manual'
                        CHECK (source_channel IN ('whatsapp_manual')),
    telegram_update_id  TEXT,
    raw_text            TEXT NOT NULL,
    classification      TEXT NOT NULL DEFAULT 'uncertain'
                        CHECK (classification IN
                            ('task','note','knowledge','follow_up','uncertain')),
    status              TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK (status IN
                            ('pending_review','confirmed','dismissed')),
    target_type         TEXT CHECK (target_type IN
                            ('work_item','note','knowledge_item',NULL)),
    target_id           TEXT,
    content_fingerprint TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_intake_telegram_update
    ON external_message_intake(telegram_update_id)
    WHERE telegram_update_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_intake_status ON external_message_intake(status);
CREATE INDEX IF NOT EXISTS idx_intake_content_fingerprint ON external_message_intake(content_fingerprint);
CREATE TABLE IF NOT EXISTS intake_audit_log (
    id         INTEGER PRIMARY KEY,
    intake_id  INTEGER NOT NULL REFERENCES external_message_intake(id) ON DELETE CASCADE,
    event      TEXT NOT NULL,
    actor      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
```

**Identity rule (AD-W7-17, frozen this pass — replaces the prior
content-hash-unique design):**

1. **Canonical local identity** is simply `external_message_intake.id` —
   NOVA never pretends to know a WhatsApp-native message identity it was
   never given (§2a).
2. **`telegram_update_id`** — where the calling Telegram handler can supply
   it (the `Update`'s own delivery identity), it is used as a **permanent**
   idempotency key, enforced by the partial unique index above: a genuine
   duplicate delivery of the same Telegram update always resolves to the
   same row, forever. This is the one case where re-processing the *same
   event* — not a *new, coincidentally similar* user action — must be
   fully idempotent.
3. **`content_fingerprint`** (sha256 of normalized `raw_text`) is a
   **secondary, scoped, time-bounded** idempotency hint only, never a
   permanent unique constraint. `capture()`'s own logic (not a database
   constraint) checks for an existing `pending_review` row with the same
   `content_fingerprint` created within a short recent window (default: 5
   minutes) and returns it instead of creating a new one — catching an
   accidental double-paste without permanently forbidding two genuinely
   distinct WhatsApp messages (sent hours or days apart) that happen to
   contain identical short text (e.g. "ok", "noted").

`capture()`'s resolution order: (1) if `telegram_update_id` is provided and
a row with that exact value already exists, return it; (2) else, if a
`pending_review` row with the same `content_fingerprint` was created within
the scoped window, return it; (3) else, create a new row. `target_type`/
`target_id` remain loose references (no FK), same discipline as
`DispatchRequest.source_id`.

`IntakeClassification` (`models.py`, not persisted):

```python
@dataclass(frozen=True)
class IntakeClassification:
    label: str          # 'task' | 'note' | 'knowledge' | 'follow_up' | 'uncertain'
    confidence: str      # 'deterministic' | 'llm_assisted' | 'uncertain'
    suggested_fields: dict[str, str]
```

## 9. Security constraints

- `raw_text` is screened with `SENSITIVE_CONTENT_PATTERN` before it is ever
  persisted; a match is rejected outright — nothing is stored.
- `content_fingerprint` is computed over normalized text only.
- `confirm()` fails closed on an invalid/unsupported `target_type` — no row
  is written to any downstream domain.
- Telegram errors are sanitized (no SQL, stack trace, or raw provider
  detail).
- No reply is ever sent back toward WhatsApp — structurally impossible,
  since `app/intake/` has no outbound network client of any kind.

## 10. Test plan

- `IntakeService.capture()` — valid text, sensitive-content rejection,
  length-bound rejection.
- **Telegram-update permanent-dedup test (explicit):** two `capture()` calls
  with the identical `telegram_update_id` return the same row; no second
  row is created, regardless of elapsed time.
- **Scoped content-fingerprint test (explicit):** two `capture()` calls with
  identical `raw_text` and no `telegram_update_id`, issued within the
  scoped window, return the same row; the same two calls issued outside the
  window create **two distinct rows** — explicitly proving distinct
  Workspace/WhatsApp objects with matching content are never silently
  collapsed (AD-W7-17).
- `classify()` — deterministic-only path for each label, `uncertain` when
  neither pass is confident, LLM-assisted path with a fake classifier
  injected and with `None` (asserting graceful fallback).
- `confirm()` — each `target_type`, invalid `target_type` fails closed,
  confirming an already-terminal row raises a typed stale-state error.
- `dismiss()` — valid, already-terminal row rejected.
- `wa_command` — direct unit test against a hand-built fake `bot_data`
  (with and without an `intake` key present); valid text, missing text
  (usage message), a **sensitive-content case**, and an **uncertain-
  classification case** asserting NOVA asks rather than commits.
- Structural test: no network-client import anywhere under `app/intake/`.
- Additive schema migration test.
- Full existing regression suite (759 passing) passes unmodified.

## 11. Acceptance criteria

1. `/wa <text>` creates a `pending_review` `ExternalMessageIntake` row and
   shows the user its classification; no `WorkItem`/`Note`/`KnowledgeItem`
   is created until the user explicitly confirms.
2. A sensitive-content `/wa` submission is rejected with no row written.
3. An uncertain classification results in NOVA asking a clarifying
   question, never an auto-committed guess.
4. A genuine duplicate Telegram delivery (same `telegram_update_id`) never
   creates a second intake row, under any time gap. Two distinct manual
   submissions with identical text sent outside the scoped window each
   create their own, independent row.
5. 8F's branch diff contains no edit to `app/main.py` and no edit to
   `build_application()`'s function signature.
6. Zero edits to `app/google_workspace/**`, `app/workspace_bridge/**` (8D),
   `app/drafting/**` (8C), `app/workspace_actions/**` (8E).
7. Full existing regression suite (759 passing) passes unmodified.

## 12. Integration dependencies

Stage 1 (parallel with 8A, 8G — no code dependency on either, and no
shared-bootstrap-file dependency). 8F's merge into the Stage 1 integration
branch, and the G1 wiring pass, are gated by Stage 1 Integration Gate G1
(`docs/WAVE_7_SHARED_CONTRACTS.md` §12), alongside 8A and 8G, before
Stage 2 may begin.

## 13. Failure behavior

See `docs/WAVE_7_SHARED_CONTRACTS.md` §9. `capture()` fails closed (no row
written) on sensitive content or invalid input; `confirm()`/`dismiss()` fail
closed on invalid state transitions; `wa_command` degrades to a clear
"unavailable" message if `intake` is absent from `bot_data`.

## 14. Technical debt deliberately deferred

- No `/waconfirm` fallback UX polish if 8G is absent at merge time —
  functional but minimal until 8G lands; intentional graceful degradation.
- `classify()`'s deterministic heuristics are intentionally simple for v1.
- `source_channel`'s single-value check constraint means a second manual-
  forward channel needs a migration to widen the `CHECK`, not a schema
  redesign.
- The scoped content-fingerprint window (default 5 minutes) is a
  configurable-in-code constant in v1, not a user-facing setting — flagged
  in case a future sprint wants it tunable.
- No edit to `app/main.py` or `build_application()`'s signature by 8F's own
  branch — owned by G1 (§7).
