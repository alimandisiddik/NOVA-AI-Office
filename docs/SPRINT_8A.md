# Sprint 8A — Workspace Connector Foundation

## Status: **FROZEN** — architecture decisions applied. Revised per Control
Tower Freeze Review: (1) Google Keep is deferred and out of active Wave 7
scope — this sprint implements no Keep capability of any kind; (2) shared
Telegram/`app/main.py` bootstrap wiring is owned by the G1 integration
branch, not by this sprint's own feature branch.

See `docs/WAVE_7_SHARED_CONTRACTS.md` §0, §3, §4 (AD-W7-01, 02, 03, 10, 11,
13, 14, 15, 16), §5a, §5b, §6, §9, §10, §11 for the cross-sprint contract
this spec implements.

## 1. Objective

Extend NOVA's already-merged, read-only Google Workspace foundation
(5C/5D/5E) with read seams for every service in Wave 7's **active** scope —
Gmail, Docs, Sheets, Slides — and prepare (but not itself wire end-to-end)
the connector for the bot. **Google Keep is explicitly out of scope for
this sprint — see §4.**

## 2. User-visible usable capability

The authorized Telegram user can run `/workspacestatus` to see, with zero
secret ever on screen, which of the seven services this connector reports
on (Gmail, Calendar, Drive, Docs, Sheets, Slides, Contacts) are currently
available, and why any are not (not configured, no scope, or simply not
implemented). No other Wave 7 sprint's actual content (inbox, documents) is
user-visible from 8A alone.

## 3. Scope

- New `app/google_workspace/gmail/` — `dtos.py`, `service.py`, `audit.py`,
  `exceptions.py`, mirroring `calendar/`'s exact layout. `GmailService`:
  `search_messages(query, max_results, correlation_id)`,
  `get_message_metadata(message_id)`, `list_thread(thread_id)`. No send/
  draft/modify/delete/trash method.
- New `app/google_workspace/docs/` — `DocsService.get_document(file_id) ->
  DocumentContent` (Docs API `documents.get`; bounded plain-text paragraph
  walk, no inline-image/comment content). No write method.
- New `app/google_workspace/sheets/` — `SheetsService.get_metadata(file_id)
  -> SpreadsheetMetadata`, `SheetsService.get_range(file_id, a1_range) ->
  RangeValues` (Sheets API `spreadsheets.get`/`spreadsheets.values.get`).
  `get_range()` requires an explicit, bounded A1 range. No write method.
- New `app/google_workspace/slides/` —
  `SlidesService.get_presentation(file_id) -> PresentationContent` (Slides
  API `presentations.get`; per-slide bounded plain-text extraction). No
  write method.
- `app/google_workspace/scopes.py`: add `GMAIL_READONLY`, `DOCS_READONLY`,
  `SHEETS_READONLY`, `SLIDES_READONLY` to `GoogleScope`; add matching
  bundle members to `ScopeBundle`. `canonicalize_scopes()`'s existing
  allowlist-and-reject logic covers every new scope automatically.
- `app/google_workspace/factory.py`: add `"gmail": ("v1",)`,
  `"docs": ("v1",)`, `"sheets": ("v4",)`, `"slides": ("v1",)` to
  `APPROVED_SERVICES`.
- New `app/google_workspace/status.py`: `ServiceCapabilityStatus`,
  `WorkspaceCapabilityReport`, and `get_workspace_capability_report(...)`
  per `docs/WAVE_7_SHARED_CONTRACTS.md` §10 — composes the existing
  `GoogleAuthenticator.get_connection_status()` plus one config/scope-based
  availability check per active service. **Exactly seven entries: gmail,
  calendar, drive, docs, sheets, slides, contacts. No `keep` entry exists.**
- **One new, additive method on the existing `GoogleAuthenticator`**
  (`app/google_workspace/auth.py`): `get_account_namespace() -> str | None`
  — returns a stable, privacy-preserving, opaque hash of the
  **authenticated Google account's own identity**, derived and validated
  the same way `client_id_hash` already is (reusing `_validate_credentials(
  )`'s existing identity/scope-validation pattern before hashing), but from
  account identity, never client identity (`docs/WAVE_7_SHARED_CONTRACTS.md`
  AD-W7-17 — this corrects the prior draft's mistaken reuse of
  `client_id_hash` for this purpose). Returns `None` when not connected.
  This is the **only** edit 8A makes to `auth.py` — every existing method
  and field is unchanged. The exact underlying account identifier this
  method hashes (e.g. a validated ID token subject claim, or an
  authenticated userinfo lookup against the already-established
  credentials) requires implementation-time verification against the
  `google-auth`/`google-auth-oauthlib` versions this repository pins — the
  frozen requirement is the semantic contract (§9), not a specific library
  call.
- `WorkspaceConnectorBundle` (`app/google_workspace/bundle.py`) — a plain
  frozen dataclass grouping `authenticator`/`gmail`/`calendar`/`drive`/
  `docs`/`sheets`/`slides`/`factory` (no `keep` field). Not a facade with
  behavior (AD-W7-01). Callers reach account identity via
  `bundle.authenticator.get_account_namespace()` — the bundle itself adds
  no separate account-identity field.
- A new, independently-testable Telegram handler function,
  `workspacestatus_command`, in `app/google_workspace/telegram.py`. It
  reads `context.bot_data.get("google_workspace")` and renders
  `WorkspaceCapabilityReport`, replying with a clear "Workspace not
  configured" message if the bot_data key is absent — so it is fully
  unit-testable today with a hand-built fake `bot_data` dict, with no
  dependency on `build_application()` (AD-W7-10).

## 4. Out of scope

- **Google Keep, in any form — package, probe, scope, DTO, test, or
  acceptance criterion. This is a hard exclusion, not a deferred detail.**
  Per Control Tower directive (`docs/WAVE_7_SHARED_CONTRACTS.md` §6,
  AD-W7-14), Keep is out of active Wave 7 scope entirely; 8A must not
  request any Keep OAuth scope under any configuration, must not implement
  a capability probe, and must not include a `keep` entry in
  `WorkspaceCapabilityReport`.
- Contacts implementation (documented seam only — §7).
- Any write method on any of the six active services.
- Any Telegram command that displays inbox/calendar/document content (8B).
- Performing an actual OAuth authentication/consent flow as part of this
  sprint's own test suite — every test uses a fake/mock client.
- Any new pip dependency.
- **Constructing `WorkspaceConnectorBundle` inside `app/main.py`, adding a
  parameter to `build_application()`'s signature, or setting
  `application.bot_data["google_workspace"]`.** These are owned by the G1
  integration branch (§6, `docs/WAVE_7_SHARED_CONTRACTS.md` §11, AD-W7-10)
  — 8A's own branch delivers the bundle/services/handler function and their
  own tests only.
- Batch-request machinery ahead of an actual consuming need.

## 5. Existing architecture reused

- `app.google_workspace.auth.GoogleAuthenticator`/`SecureFileTokenStorage`
  — reused verbatim across all six active services, with **exactly one
  additive method** (`get_account_namespace()`, §3) — every existing
  method, field, and validation path is unchanged; `_validate_credentials(
  )`'s existing identity/scope-validation pattern is reused (not
  reimplemented) as the basis for the new method's own validation step.
- `app.google_workspace.factory.GoogleClientFactory` — unchanged except four
  additive `APPROVED_SERVICES` entries.
- `app.google_workspace.calendar.{service,dtos,audit,exceptions}` — the
  structural template every new service (`gmail/`, `docs/`, `sheets/`,
  `slides/`) copies.
- `app.config.Settings.google_client_secrets_path`/`.google_token_storage_
  path`/`.google_oauth_port` — reused unchanged; **no new setting is added
  by this sprint** (Keep's opt-in flag from the prior draft has been
  removed along with Keep itself).

## 6. Owned files/packages

- `app/google_workspace/{gmail,docs,sheets,slides}/*.py` — new,
  8A-exclusive.
- `app/google_workspace/scopes.py`, `factory.py` — extend only.
- `app/google_workspace/auth.py` — **extend only**, one new method
  (`get_account_namespace()`); every existing method/field/behavior
  unchanged.
- `app/google_workspace/status.py`, `bundle.py`, `telegram.py` — new,
  8A-exclusive.
- New test files: `tests/google_workspace/gmail/test_service.py`,
  `tests/google_workspace/docs/test_service.py`,
  `tests/google_workspace/sheets/test_service.py`,
  `tests/google_workspace/slides/test_service.py`,
  `tests/google_workspace/test_status.py`,
  `tests/google_workspace/test_account_namespace.py` (new — see §10),
  `tests/test_workspace_status_handler.py` (direct handler-function test
  against a fake `bot_data`, not a `build_application()`-level test).

**Not owned by 8A (see §7 and `docs/WAVE_7_SHARED_CONTRACTS.md` §11):**
`app/main.py`, `build_application()`'s signature in `app/telegram_bot.py`,
`app/config.py`, `.env.example`.

## 7. Shared-file rules

Per the revised AD-W7-10 (`docs/WAVE_7_SHARED_CONTRACTS.md` §11):

- 8A's branch **does not edit** `app/main.py` or `build_application()`'s
  function signature. `WorkspaceConnectorBundle` construction and the
  `google_workspace` parameter/`bot_data` wiring are added once, in one
  coordinated pass, by the **G1 integration branch** — not by 8A.
- 8A's branch **may optionally** add a single, isolated
  `application.add_handler(CommandHandler("workspacestatus",
  workspacestatus_command))` line inside `build_application()`'s existing
  command-registration block, if branch-level end-to-end testability
  against a real `Application` object is wanted. This is the only
  `app/telegram_bot.py` edit 8A's own branch may make, and it must never be
  paired with a signature or `bot_data`-assignment edit. If 8A's branch does
  not add this line, G1 adds it.
- `app/config.py`/`.env.example` — 8A requires **zero** edits (no Keep
  opt-in flag, no other new setting).
- `app/knowledge/**`, `app/control_tower/**`, `app/agent_assignment/**`,
  `app/brief/**`, `app/dashboard/**`, `app/dispatch/**`, `app/nightshift/**`,
  `app/dissertation/**`, `app/memory/**` — no dependency, no edit.

**Contacts (documented, not implemented — AD-W7-13):**

```python
# Documented, not implemented in 8A. No scope requested, no dependency
# added, no call site anywhere in the codebase.
class ContactsService(Protocol):
    def search_contacts(self, query: str) -> tuple[ContactSummary, ...]: ...
```

## 8. Data models/interfaces

No new persisted table. New DTOs (mirroring `calendar/dtos.py`'s exact
privacy discipline):

```python
# gmail/dtos.py
@dataclass(frozen=True)
class MessageSummary:
    message_id: str
    thread_id: str
    subject: str
    sender_alias: str        # "sender_" + sha256(from-address)[:12]
    received_at: datetime
    snippet: str               # bounded (<=200 chars), never full body
    has_attachments: bool
    label_ids: tuple[str, ...]

@dataclass(frozen=True)
class ThreadSummary:
    thread_id: str
    message_count: int
    messages: tuple[MessageSummary, ...]

@dataclass(frozen=True)
class MessageSearchResult:
    messages: tuple[MessageSummary, ...]
    truncated: bool

# docs/dtos.py
@dataclass(frozen=True)
class DocumentContent:
    file_id: str
    title: str
    paragraphs: tuple[str, ...]
    truncated: bool

# sheets/dtos.py
@dataclass(frozen=True)
class SpreadsheetMetadata:
    file_id: str
    title: str
    sheet_titles: tuple[str, ...]

@dataclass(frozen=True)
class RangeValues:
    file_id: str
    a1_range: str
    rows: tuple[tuple[str, ...], ...]

# slides/dtos.py
@dataclass(frozen=True)
class SlideContent:
    slide_index: int
    text_fragments: tuple[str, ...]

@dataclass(frozen=True)
class PresentationContent:
    file_id: str
    title: str
    slides: tuple[SlideContent, ...]
    truncated: bool
```

`WorkspaceCapabilityReport` per `docs/WAVE_7_SHARED_CONTRACTS.md` §10 —
always exactly **seven** `ServiceCapabilityStatus` entries
(`gmail`/`calendar`/`drive`/`docs`/`sheets`/`slides`/`contacts`), `contacts`
always `available=False, reason='not_implemented'`. **No `keep` value is a
valid `service` string anywhere in this sprint's code or tests.**

## 9. Security constraints

- `GmailService`/`DocsService`/`SlidesService` never return full raw
  content beyond their documented bound — no method exists to dump an
  entire mailbox, document, or presentation in one call.
- `sender_alias` reuses `CalendarService`'s exact
  `"sender_" + sha256(...)[:12]` hashing convention.
- `/workspacestatus` never renders a file path, client secret, token value,
  or account email — only booleans, reason codes, and the existing
  `client_id_hash`.
- **`get_account_namespace()` never returns, logs, or persists the raw
  account identifier it hashes** (email, subject ID, or whatever underlying
  value is used) — only the opaque hash ever leaves `GoogleAuthenticator`,
  matching `client_id_hash`'s existing discipline exactly.
  **`get_account_namespace()`'s output must never equal `client_id_hash`'s
  output for the same credentials** — the two identify different things
  (account vs. OAuth client) and must be computed from different inputs,
  not merely from different label strings on the same input.
- `GmailService.search_messages()`'s `query` parameter and
  `SheetsService.get_range()`'s `a1_range` parameter are both validated
  against strict, bounded patterns before reaching the Google API.
- No credential, token, or OAuth flow runs during the test suite.

## 10. Test plan

- Per new service (`GmailService`, `DocsService`, `SheetsService`,
  `SlidesService`) — bounded results, truncation flag, not-found/malformed-
  response mapped to the existing typed category taxonomy.
- `SheetsService.get_range()` — valid A1 range, malformed/unsafe range
  rejected before any API call.
- Scope registry — every new scope accepted by `canonicalize_scopes()`;
  unknown/unapproved scopes still rejected (regression). **A structural
  test asserts no scope string containing `keep` exists in
  `app/google_workspace/scopes.py`.**
- Factory — every new `(service, version)` pair now approved; every other
  combination still rejected (regression). **A structural test asserts
  `"keep"` is not a key in `APPROVED_SERVICES`.**
- `get_workspace_capability_report()` — fully configured and unconfigured
  fixtures; asserts exactly seven entries, no entry ever contains a secret/
  token/path value; `contacts` is always `not_implemented`; **no entry with
  `service == "keep"` exists.**
- `workspacestatus_command` — direct unit test against a hand-built fake
  `bot_data` dict (both with and without a `google_workspace` key present)
  — no `build_application()` dependency. No secret/token/path leakage for
  any combination; unauthorized user rejected.
- **`get_account_namespace()` (explicit, named — the test this correction
  pass exists to require):**
  - Not connected / no valid credentials → returns `None`, no exception.
  - Two fake credential fixtures representing **different Google accounts**
    (different underlying account identifiers) produce **different**
    `account_namespace` hashes.
  - The **same** fake credential fixture, hashed twice (e.g. across a
    simulated reconnect), produces the **same** `account_namespace` hash —
    stability is required, not just distinctness.
  - `get_account_namespace()`'s output is asserted **not equal** to
    `client_id_hash`'s output for the same fixture, for at least one
    fixture — a direct, explicit regression guard against ever
    reintroducing the corrected mistake.
  - The raw underlying identifier used to compute the hash never appears in
    the returned value, in any log call, or in any persisted field.
- **Regression/isolation test:** importing `app.main` and running its
  startup sequence with no Google Workspace configuration present never
  raises and never attempts a network call for any of the six active
  services.
- Full existing regression suite (759 passing) passes unmodified.

## 11. Acceptance criteria

1. With Workspace unconfigured, running `python -m app.main` completes
   startup successfully (unaffected by 8A, since 8A's branch does not touch
   `app/main.py` — this criterion is really validated at G1, but 8A's own
   package must not raise on import or construction under any
   configuration).
2. Calling `get_workspace_capability_report()` directly (without
   `build_application()`) with Workspace unconfigured returns all seven
   services `available=False` with a `not_configured` (or `not_implemented`
   for Contacts) reason, with zero network calls attempted.
3. No file under `app/google_workspace/{gmail,docs,sheets,slides}/`
   contains a reference to any write-capable API method — verified by a
   structural grep test.
4. **No file under `app/google_workspace/` references Keep in any form**
   (no `keep` scope, no `KeepService`, no `keep` factory entry, no `keep`
   `WorkspaceCapabilityReport` entry) — verified by a structural grep test
   asserting the string `keep` (case-insensitive) does not appear in
   `app/google_workspace/**` except in comments explicitly citing the
   deferral decision, if any.
5. 8A's branch diff contains no edit to `app/main.py` and no edit to
   `build_application()`'s function signature in `app/telegram_bot.py` —
   verified directly against the branch diff, not merely asserted.
6. `GoogleAuthenticator.get_account_namespace()` returns distinct, stable
   hashes for distinct accounts, the same hash across repeated calls for
   the same account, `None` when unconnected, and a value that is never
   equal to `client_id_hash` for the same credentials — every existing
   `GoogleAuthenticator` method/field is otherwise unchanged (`git diff`
   against `auth.py` shows only the new method added).
7. Full existing regression suite (759 passing) passes unmodified with
   exactly 759 + 8A's new test count passing, zero failures, zero skips.

## 12. Integration dependencies

Stage 1 (parallel with 8F, 8G — no code dependency on either, and, per this
revision, no shared-bootstrap-file dependency either). 8A's merge into the
Stage 1 integration branch, and the subsequent G1 wiring pass, are gated by
Stage 1 Integration Gate G1 (`docs/WAVE_7_SHARED_CONTRACTS.md` §12),
alongside 8F and 8G, before Stage 2 may begin. 8B (Stage 2) hard-depends on
8A's `GmailService`/`CalendarService` being merged (post-G1); 8C/8D
(Stage 3) additionally depend on `DocsService`/`SheetsService`/
`SlidesService`.

## 13. Failure behavior

See `docs/WAVE_7_SHARED_CONTRACTS.md` §9. Specifically for 8A: no
configured service ever raises on construction; every read method maps
provider failures to the existing typed category taxonomy; an unconfigured
connector reports every service unavailable rather than failing bot
startup.

## 14. Technical debt deliberately deferred

- No edits to `app/dispatch/**`, `app/control_tower/**`, `app/knowledge/**`,
  `app/agent_assignment/**`, `app/brief/**`, `app/dashboard/**`,
  `app/nightshift/**`, `app/dissertation/**`, `app/memory/**`.
- No new write method on any of the six active services.
- No `ContactsService` implementation, no People API scope requested.
- **No Google Keep implementation of any kind — see §4.**
- No edit to `app/main.py` or `build_application()`'s signature by 8A's own
  branch — owned by G1 (§7).
- Contacts remains documented-only; if a future sprint needs it, the
  `ContactsService` Protocol above is the agreed starting shape.
- `WorkspaceConnectorBundle` is a plain data holder; if a future sprint adds
  behavior to it rather than to one of the per-domain services, that's a
  signal AD-W7-01's "no facade" decision should be revisited.
- Google Keep may be revisited in a future wave only after the four
  conditions in AD-W7-14 are met — this is a deliberate, recorded
  deferral, not an oversight.
- **`get_account_namespace()`'s exact underlying data source is
  implementation-time-verified, not frozen here** (`docs/WAVE_7_SHARED_
  CONTRACTS.md` AD-W7-17) — the architecture fixes the semantic contract
  (stable per-account, distinct across accounts, never OAuth-client
  identity, never a raw value once returned) and requires the implementer
  to confirm against current `google-auth`/`google-auth-oauthlib` library
  behavior which validated account-level identifier is actually available
  from the credentials this repository already requests
  (`userinfo.email`/`userinfo.profile`). This is a deliberate, narrow gap
  left for implementation, not an oversight.
