# Wave 7 Shared Contracts — Executive Operations & Workspace Automation

> **8E revision control — Sprint 8E architecture revision (post-G3):**
> `docs/SPRINT_8E.md` is the controlling Stage 4 contract. Any earlier text
> in this document describing broad 8E Gmail, Calendar, Drive, Docs, Sheets,
> or Slides write execution is superseded for Sprint 8E acceptance and must be
> read as future roadmap only. The executable MVP is one private
> `create_docs_file` action sourced from a current ready 8C `docs_memo`.
> `share_file` and all other Workspace writes are out of scope.

## Status: **ARCHITECTURE FROZEN, PENDING FINAL CONTROL TOWER SIGN-OFF** —
revised per Control Tower Freeze Review to (1) move Stage 1 shared bootstrap
wiring to G1 integration-branch ownership, (2) place Google Keep on
**HOLD / deferred, out of active Wave 7 scope**, (3) redesign Workspace
provenance identity around stable external identifiers rather than content
hashing alone, and (4) make the active six-service Google Workspace matrix
explicit; then further corrected per a final Control Tower pass to
(5) source `account_namespace` from the authenticated Google **account**'s
own identity rather than the OAuth client's identity, and (6) extend the
same shared-bootstrap integration-ownership principle from Stage 1 to
Stage 3 (8C/8D), since it is likewise a genuinely parallel stage.
Implementation not started.

Baseline: `594c36f` (merge: Sprint 7E executive dashboard skeleton), branch
`arch/wave7-shared-contracts`. Wave 6 (Sprints 7A–7E) is merged and complete,
759 passing tests. This document governs seven sprints — **8A–8G** — across
four sequential stages, each stage closed by a named integration gate before
the next stage starts.

## 0a. Sprint 8E Workspace Write Safety revision (AD-8E-01…06)

This post-G3 revision narrows Stage 4 without changing stages 1–3. The single
MVP external-write spine is `docs_memo -> create_docs_file -> dispatch ->
canonical approval -> freshness/integrity -> CAS -> typed Docs write ->
succeeded|outcome_unknown`. Its core invariant is **no valid approval = no
Google write**.

- **AD-8E-01 — freshness:** 8E must use an additive 8C public
  `get_current_ready_action(action_id)` contract. It proves both
  `ready_for_action` and absence of a successor whose `supersedes_id` is the
  source action. A fingerprint is integrity-only and cannot substitute for
  current-revision validation. 8E makes no cross-domain raw SQL query.
- **AD-8E-02 — outcome uncertainty:** WorkspaceAction adds terminal
  `outcome_unknown` for ambiguous post-submission failures. It never retries
  automatically and exposes reconciliation-required status without content.
- **AD-8E-03 — scopes:** OAuth compatibility uses
  `required_scopes ⊆ granted_scopes`, retaining validation against the approved
  registry. The frozen capability is Docs blank-document creation and body
  insertion through `documents.create` then `documents.batchUpdate` with
  `InsertTextRequest`. For that exact app-created-resource implementation,
  `drive.file` is sufficient and is the least-privilege write-scope choice;
  it is not claimed as the only scope that can authorize Docs operations
  generally. Missing capability fails closed without disabling reads.
- **AD-8E-04 — runtime:** 8E owns minimal configuration-aware bundle wiring
  using the existing `GoogleAuthenticator` and `GoogleClientFactory`. Startup
  performs no OAuth, refresh, browser, or network activity. Missing write
  capability disables only the write action.
- **AD-8E-05 — dispatch:** append `workspace_action` as an additive source,
  map `source_id` to WorkspaceAction ID, and use a specific approval-required
  `workspace_write` capability with `google_workspace_adapter`. Existing
  source semantics stay intact.
- **AD-8E-06 — risk:** `create_docs_file` is
  `MODERATE_EXTERNAL_WRITE`, private/default-private and manually reversible,
  but always explicitly approved. Bare affirmative conversation cannot grant
  approval; an exact active action-specific choice is required.

### Shared data, reconciliation, and documentation rules

- WorkspaceAction persistence/audit is metadata-only and never duplicates 8C
  body content, OAuth material, secrets, or raw provider responses. The
  immutable 8C action remains canonical content storage.
- All approval expiry and WorkspaceAction timestamps use an injectable
  application UTC clock and ISO-8601 UTC values; no DB implicit/local time
  decides action safety.
- `outcome_unknown` safely displays action ID, type, attempt timestamp,
  sanitized provider reference, and correlation ID, plus reconciliation
  required. A future explicit admin-safe reconciliation mechanism is mandatory
  before irreversible actions such as Gmail send.
- Before live OAuth reliance, verify Google Auth Platform publishing/testing
  state and token-lifetime behavior in the actual environment. Do not assume a
  refresh-token lifetime in architecture documentation.
- Architecture and review documentation must use repo-relative paths and omit
  absolute home paths, personal emails, OAuth client IDs, tokens, credentials,
  and machine-specific personal identifiers unless technically required.
  Technical provenance such as commit hashes remains allowed.

---

## 0. Grounding — what already exists (inspected, not assumed)

| Concern | Canonical module | State found |
|---|---|---|
| Google OAuth (desktop flow, token storage, client factory) | `app/google_workspace/{auth,factory,scopes}.py` | Merged (Sprint 5C). Fail-closed, desktop `InstalledAppFlow` + `run_local_server`, atomic symlink-safe local token file, identity+scope validation on every credential load, non-sensitive audit trail (`AuthAuditLog`), `get_connection_status()` returns booleans/hashes only — never a token or path. |
| Google Calendar (read-only) | `app/google_workspace/calendar/**` | Merged (5D). `CalendarService`: `list_today`, `list_week`, `search_events`, `get_free_busy`, `detect_conflicts`, `build_meeting_brief`, `prepare_event_draft` (local DTO, no API call). Privacy-scrubbing DTOs, bounded windows/results, typed sanitized errors, audit sink. **No mutation endpoint.** |
| Google Drive (read-only) | `app/google_workspace/drive/**` | Merged (5E). `DriveReadService`: folder-allowlisted `search_files`/`get_metadata`, `export_working_copy` (Docs/Sheets/Slides → PDF/XLSX), `download_working_copy` (bounded MIME allowlist). Path/symlink/size-limit hardened. **No write endpoint.** |
| Gmail / Docs-native / Sheets-native / Slides-native / Contacts | — | **None exist.** No package, scope, service, or test for any of these five today. Docs/Sheets/Slides are reachable today only indirectly, as opaque PDF/XLSX exports via Drive — no structural (paragraph/range/slide) read exists. |
| Google Keep | — | Does not exist, and **is not being built this wave** — see §6. On hold pending a future, separately-scoped architecture decision. |
| Approved OAuth scopes | `app/google_workspace/scopes.py` | `userinfo.email`, `userinfo.profile`, `calendar.readonly`, `drive.readonly` only. Zero write scope anywhere. |
| OAuth client model | `GoogleAuthenticator.reconnect()` | `InstalledAppFlow.from_client_secrets_file(...).run_local_server(host="localhost", ...)` — already a **Desktop App**, user-consent OAuth client. This is a **personal end-user credential**, not a service account, and has no domain-wide-delegation capability. |
| Config wiring | `app/config.py` | `google_client_secrets_path`, `google_token_storage_path`, `google_oauth_port` already exist, optional at startup, required together. **Not read by `app/main.py`/`app/telegram_bot.py` at all** — 5C/5D/5E shipped services with zero runtime wiring; no Telegram command reads Calendar or Drive today. |
| Runtime wiring | `app/main.py`, `app/telegram_bot.py` | **Zero Google Workspace construction or commands exist in either file.** 8A is the first sprint to add Workspace read services; Stage 1's actual bootstrap wiring is owned by the G1 integration branch — see §11. |
| Approval authority | `app/dispatch/approvals.py` `ApprovalService` | Canonical since Wave 3. `request_approval()` **always** requires an existing `DispatchRecord` — there is no freestanding approval mechanism. |
| Dispatch idempotency | `app/dispatch/service.py` `DispatchService.create_dispatch()` | Already implements `(source_type, source_id, idempotency_key)` uniqueness, `find_idempotent()`, identical-request short-circuit, `DuplicateDispatchError` on conflicting reuse. **Reused, not reinvented.** |
| Agent/capability closed vocabulary | `app/dispatch/registry.py` `AGENT_REGISTRY` | `workspace_agent` is registered (category `workspace`) but capabilities are `{read_only, draft_only}` only, `adapter_id="local_deterministic"` — **no execution adapter**. Matches the previously documented gap in `docs/WAVE_6_SHARED_CONTRACTS.md` AD-W6-06/§7. |
| Conversation/pending-choice state | — | Does not exist anywhere (repo-wide search: zero hits for `pending_interaction`/`PendingChoice`/`ConversationState`). `app.memory.models.WorkSession` is a project retrospective log — evaluated and rejected as a host (AD-W7-08). |
| Wave 6 parallel-integration precedent | `tests/test_wave6_integration.py`, `docs/CURRENT_SPRINT.md`'s "Wave 6 — Integration review" entry | Wave 6 already proved the pattern this revision now leans on more heavily for Stage 1: three independently-developed sprints (7A/7B/7D) merged onto a shared `integration/wave6-core` branch, with a **dedicated cross-sprint integration test file** verifying combined `build_application()` wiring, initialization order, and no double-registration — written once, by the integration pass, not by any single feature sprint. §11 reuses this exact precedent for Wave 7 Stage 1's G1 gate. |
| Existing Telegram commands that must remain unaffected | `app/telegram_bot.py` | `/run`, `/runstatus`, `/cancelrun`, `/runapprove`, `/capture`, `/today`, `/workitem`, `/execbrief`, `/knowledgesource`/`/knowledgeitem`/`/knowledgequery`, `/assignments`/`/assignmentstatus`, `/nightshift`/`/nightstatus`/`/nightqueue`/`/wake`, `/dispatch`/`/approve`/`/reject`/etc. — all pre-existing, all registered before the generic text fallback. |
| Provenance/candidate-state pattern | `app.knowledge.models.KnowledgeSource` (7B) | Already has `source_type` including `drive_file`/`calendar_event`, `origin_system` including `google_drive`/`google_calendar`, and an opaque `origin_ref` — reusable as a *pattern* (not shared tables) for 8D/8F's own candidate models. |
| Installed Google dependencies | `requirements.txt` / `pyproject.toml` | `google-auth>=2.30.0`, `google-auth-oauthlib>=1.2.0`, `google-api-python-client>=2.130.0`. These cover every **active** Google API this document discusses (Gmail, Docs, Sheets, Slides) through the same `googleapiclient.discovery.build(service, version, credentials=...)` factory already used for Calendar/Drive — no new pip dependency for any Wave 7 sprint. |

**Consequence:** Wave 7 extends an already-hardened, read-only foundation.
No sprint re-implements OAuth, token storage, or the client factory. No
Wave 7 sprint requires a new pip package. No Wave 7 sprint touches Google
Keep.

---

## 1. Frozen Wave 7 sprint set — exactly seven, no more

| Sprint | Name | New package (owner) |
|---|---|---|
| 8A | Workspace Connector Foundation | extends `app/google_workspace/` |
| 8B | Inbox & Calendar Intelligence | `app/workspace_intel/` |
| 8C | Drafting & Document Operations | `app/drafting/` |
| 8D | Workspace → Control Tower Integration | `app/workspace_bridge/` |
| 8E | Approval-Gated Workspace Actions | `app/workspace_actions/` (+ additive files in `app/google_workspace/` and `app/dispatch/`) |
| 8F | `/wa` External Message Intake | `app/intake/` |
| 8G | Conversational Control & Contextual Confirmation | `app/conversation/` |

No eighth sprint. Anything not fitting these seven is deferred technical
debt (§14 of each sprint doc, and §6/§15 below) — **Google Keep is the
primary example of this: a target capability from the original brief,
explicitly moved out of active Wave 7 scope by Control Tower directive, not
dropped from the roadmap.**

## 2. Dependency graph with named stage gates

```
ARCHITECTURE FREEZE (this document)
        |
        v
STAGE 1 -- PARALLEL FEATURE WORK:  8A  8F  8G   (independent packages/tests only;
        |                                        no sprint edits shared bootstrap files)
        v
   === STAGE 1 INTEGRATION GATE (G1) ===   owns build_application() composition,
        |                                   app/main.py construction, handler
        |                                   ordering validation -- see section 11
        v
STAGE 2:              8B             (hard dependency: 8A's Gmail/Calendar reads)
        |
        v
   === STAGE 2 INTEGRATION GATE (G2) ===
        |
        v
STAGE 3 -- PARALLEL FEATURE WORK:  8C  8D   (independent packages/tests only;
        |                                    no sprint edits shared bootstrap files)
        v
   === STAGE 3 INTEGRATION GATE (G3) ===   owns build_application() composition,
        |                                   app/main.py construction, handler
        |                                   registration dedup for Stage 3 -- see section 11
        v
STAGE 4:              8E             (hard dependency: 8A connector, 8C prepared
                                       content, 8D committed candidates, 8G confirmation)
        |
        v
FINAL WAVE 7 INTEGRATION (G4)
```

**Why this graph is safe, checked against actual code dependencies, not
assumed:**

- **8A/8F/8G are mutually independent at the package level**, and — per this
  revision — are now also independent at the *shared-file* level: none of
  the three edits `app/main.py`'s service construction, `build_application(
  )`'s signature, or its `bot_data` wiring. Only 8G edits a shared file
  directly (`handle_text`'s body — a narrow, self-contained, zero-collision
  edit no other Stage 1 sprint touches). See §11 for the full mechanism and
  why the previously-frozen "signature append order" was superseded.
- **8B genuinely requires 8A** — it composes `GmailService`/`CalendarService`
  reads that do not exist before 8A (and G1) merge.
- **8C and 8D are mutually independent**, and — per this revision — are now
  also independent at the *shared-file* level, the same way 8A/8F/8G are in
  Stage 1: neither edits `app/main.py`'s service construction or
  `build_application()`'s signature. 8C (local content preparation) never
  calls Control Tower. 8D (candidate creation) never calls
  `app/drafting/`. Both read 8A/8B's existing public methods only. See §11
  for the G3-owned wiring mechanism, which mirrors G1's exactly.
- **8E genuinely requires all of Stage 1–3.** Sending a drafted email needs
  8C's `PreparedWorkspaceAction`; committing a Workspace action tied to a
  candidate needs 8D's committed-state contract; its confirmation UX needs
  8G (Stage 1, already merged by Stage 4); its connector needs 8A's write-
  service extension.
- **No reordering is required.** This revision changes *how* Stage 1's
  shared files are integrated (§11), not *what* depends on *what*.

## 3. Contract evaluation — what is new vs. reused vs. deferred

### 3.1 Gmail — NEW, mirrors Calendar's file layout exactly

`app/google_workspace/gmail/` (`dtos.py`, `service.py`, `audit.py`,
`exceptions.py`). Read-only: `search_messages`, `get_message_metadata`,
`list_thread`. No send/draft/modify/delete/trash method exists in 8A.

### 3.2 Calendar/Drive — REUSE, no new read contract

Consumed as-is; 8A adds no new method to either.

### 3.3 Docs, Sheets, Slides — NEW, first-class read seams (not just Drive export)

An opaque PDF/XLSX export cannot answer "what does paragraph 3 say" or
"what is in cell B7" — it has no structure. This wave adds three new
sibling packages, each mirroring Calendar's exact discipline:

- `app/google_workspace/docs/` — `DocsService.get_document(file_id) ->
  DocumentContent` (Docs API `documents.get`; bounded plain-text paragraph
  walk).
- `app/google_workspace/sheets/` — `SheetsService.get_metadata(file_id)`
  and `SheetsService.get_range(file_id, a1_range) -> RangeValues` (Sheets
  API `spreadsheets.get`/`spreadsheets.values.get`), bounded to an explicit
  A1-notation range.
- `app/google_workspace/slides/` — `SlidesService.get_presentation(file_id)
  -> PresentationContent` (Slides API `presentations.get`), bounded to
  per-slide plain-text extraction.

Existing `DriveReadService.export_working_copy()` (PDF/XLSX) remains
available unchanged as a separate, coarser-grained capability.

### 3.4 Google Keep — DEFERRED, out of active Wave 7 scope

**Google Keep is on hold. It is not part of the active Wave 7
implementation scope, by explicit Control Tower decision.** See §6 for the
full deferral record and the conditions under which it may be revisited in
a future wave. No package, no scope, no probe, no read, no write, no test,
and no acceptance criterion anywhere in Wave 7 depends on or references
Keep as an active capability.

### 3.5 Contacts — DEFERRED, documented only, non-blocking

No existing support, and no sprint in the frozen 8A–8G set has a
user-facing capability that actually requires contact lookup (checked
explicitly against every sprint's §2/§3 — none call for it). 8A documents
(does not implement) a `ContactsService` seam signature for a possible
future sprint. Contacts is not a Wave 7 blocker under any circumstance —
see AD-W7-13.

### 3.6 `WorkspaceConnector` — a boundary, not one monolithic class

"Workspace Connector Foundation" is realized as the existing shared
`GoogleAuthenticator`/`GoogleClientFactory` pair feeding **six** independent,
single-domain read services covering the active scope — Gmail, Calendar,
Drive, Docs, Sheets, Slides — plus one aggregating, read-only
**`WorkspaceCapabilityReport`** (§10). Keep is not one of these six and does
not appear in the connector bundle at all (§6). Contacts is documented only,
also absent from the runtime bundle. No god-object `WorkspaceConnector`
class. See AD-W7-01.

### 3.7 New shared/candidate models — evaluated one at a time

- **`WorkspaceSourceRef`** — NEW, owned by 8D. Distinct from 7B's
  `KnowledgeSource` (committed provenance) — exists specifically for
  **candidate** state before a `WorkItem`/`Decision`/`KnowledgeItem` is
  committed. Identity is now anchored on **stable external identity**
  (`source_system`, `account_namespace`, `external_source_type`,
  `external_source_id`) — a content fingerprint is retained only as a
  secondary, non-identity hint. See AD-W7-17 and §8's dedicated identity
  section for why this replaced the earlier content-hash-only design.
- **`PreparedWorkspaceAction`** — NEW, owned by 8C. Local-only content
  preparation record (email/reply text, Docs memo text, Sheets change-set,
  Slides outline) with **zero** Google API call in its own code path
  (AD-W7-05). Covers four content types in the active scope — Gmail
  reply/new, Docs memo, Sheets change, Slides outline — **not** Keep notes
  (removed, §6).
- **`WorkspaceAction`** — NEW, owned by 8E. A thin domain record whose
  `payload_ref` is handed to the existing `DispatchService.create_dispatch()`
  — not a new dispatch system, exactly mirroring 7D's `AgentAssignment`.
  Its action-type vocabulary covers the active six services only.
- **`ExternalMessageIntake`** — NEW, owned by 8F. Structurally similar to
  `WorkspaceSourceRef` (candidate → committed) but a distinct table with no
  shared code, since 8F must remain Stage-1-parallel-safe with zero
  dependency on 8D. Its own identity model is two-tier — a permanent
  Telegram-update identity where available, and a scoped (time-bounded)
  content fingerprint hint, never a permanent content-only key. See
  AD-W7-17.
- **`PendingInteraction`** — NEW, owned by 8G. No existing table represents
  ephemeral question/choices/expiry/consumption state (§0).

No Wave 7 model duplicates `WorkItem`, `Decision`, `Approval`,
`AgentAssignment`, `KnowledgeItem`, or `KnowledgeSource`. Every candidate-to-
committed transition in 8D/8F, and every prepared-to-executed transition in
8C→8E, ends by calling an **existing** canonical service's existing public
write method — never a direct write to another domain's table.

### 3.8 8C vs. 8E responsibility boundary — content actions vs. structured actions

`WorkspaceActionType` splits into two kinds:

- **Content actions** (`create_gmail_draft`, `send_email`, `reply_email`,
  `create_docs_file`, `write_sheets`, `create_slides`/`update_slides`) —
  require a `prepared_workspace_action_id` reference to an 8C
  `PreparedWorkspaceAction` in `status='ready_for_action'`. 8E never
  invents content; it only ever executes what 8C already prepared and the
  user already reviewed.
- **Structured actions** (`create_calendar_event`, `update_calendar_event`,
  `share_file`) — carry their own small set of directly-specified,
  already-bounded parameters and have no drafting step.

This means 8C and 8E's ownership never overlaps: 8C never calls a Google
write endpoint (structurally enforced), and 8E never generates content.

---

## 4. Architecture decisions

### AD-W7-01 Workspace Connector boundary — no monolithic connector class

**Decision:** "Workspace Connector Foundation" is the existing shared
`GoogleAuthenticator`/`GoogleClientFactory` pair feeding independent
per-domain services across the six active services, plus one read-only
`WorkspaceCapabilityReport` aggregator. No `WorkspaceConnector` facade
class.

**Alternative considered:** a single facade exposing `.gmail`, `.calendar`,
`.docs`, etc. **Rejected:** every per-domain service is already
independently constructible/optional/testable; a facade would force all
active domains to initialize together for no capability gain.

### AD-W7-02 Scope separation — read scopes ship in Stages 1–3; write scopes ship only with 8E

**Decision:** 8A/8B/8C request **read-only** scopes exclusively across all
six active services. No write scope (`gmail.compose`, `gmail.send`,
`calendar.events`, `drive.file`, `documents` (write), `spreadsheets`
(write), `presentations` (write)) is requested by any Stage 1–3 sprint.
8E is the only sprint that requests write scopes.

**Alternative considered:** request the union of all needed scopes up front
in 8A. **Rejected:** violates least-privilege; Google's own desktop-OAuth
UX already re-prompts consent on scope changes, so a second consent step at
8E's activation is expected, not a defect.

### AD-W7-03 Read/write separation enforced at the file/method level, not just by scope

**Decision:** every read method (8A/8B/8C) lives in a file with no network
path to a write endpoint even if broader scopes were somehow granted. 8E's
write capability is added as a **new, separate class**
(`app.google_workspace.docs.service.DocsWriteService`) with no method
overlap with the read class, so "no read-service class contains a mutating
call" is grep-verifiable per service.

**Revised at G4 (implementation reality, no behavior change):** the
originally-frozen text called for a physically separate
`write_service.py` file per service. The as-built G4 state instead keeps
`DocsWriteService` in the same file as the read-only `DocsService`
(`app/google_workspace/docs/service.py`) as a distinct class with a
disjoint method surface (`DocsService.get_document` vs.
`DocsWriteService.create_private_document`). The safety property this
decision exists to guarantee — a read-service class can never reach a
mutating call — holds at the class boundary exactly as it would at the
file boundary; this is a file-organization correction, not a relaxation
of AD-W7-03's invariant.

### AD-W7-04 External-write approval mechanism — reuse `DispatchService`/`ApprovalService`, close the documented `GEMINI`-adapter gap

**Decision:** 8E extends `workspace_agent`'s registered capabilities
(`app/dispatch/registry.py`) to include `workspace_write`, with a
registered `adapter_id` (`google_workspace_adapter`) reserved for a future
generic-dispatch execution path. `WorkspaceAction`'s idempotency/
replay-protection is `DispatchService.create_dispatch()`'s existing
uniqueness, verbatim — not reimplemented.

**Files touched outside Wave 7's new packages (flagged for extra
scrutiny):** `app/dispatch/registry.py` — 8E-exclusive, Stage 4 only, no
parallel-branch collision risk since 8E is the sole Stage 4 sprint.

**Revised at G4 (implementation reality, safer than originally drafted):**
`app/dispatch/adapters.py` was **not** touched, and no
`GoogleWorkspaceAgentAdapter` class exists. `workspace_agent`'s registered
`adapter_id` (`google_workspace_adapter`) does not resolve to any entry in
`app.dispatch.adapters._ADAPTERS` — calling it raises
`DispatchUnavailableError`. This is intentional, not an oversight: the
as-built G4 invariant is —

- `DispatchService`/`ApprovalService` provide the canonical
  create/approve/reject linkage and idempotency for a `workspace_action`
  dispatch, exactly as for every other source type — nothing more.
- `WorkspaceActionService` (`app/workspace_actions/service.py`) owns the
  entire external-write execution state machine — freshness/integrity
  re-validation, the `approved -> executing` CAS claim, the single typed
  `DocsWriteService` call, and finalization — independently of
  `DispatchService.dispatch()`/`retry_dispatch()`.
- Generic dispatch execution (`/dispatch`, `/retrydispatch`, or the
  `ApprovalService`-only fallback branch of `/approve`) **cannot** reach a
  real Workspace write: `workspace_write` capability is structurally
  rejected for any dispatch not sourced as `workspace_action`
  (`DispatchService._validate_request`), and even a `workspace_action`
  dispatch's own `adapter_id` resolves to nothing executable.
- `google_workspace_adapter` is reserved vocabulary for a possible future
  generic-execution path; it is **not a live execution adapter** in Sprint
  8E and must not be treated as one by any future sprint without an
  explicit new decision.

### AD-W7-05 A real Google-side write — including a Gmail draft or a Docs/Sheets/Slides mutation — is always EXTERNAL_WRITE; 8C never performs one

**Decision:** 8C's `PreparedWorkspaceAction` never calls a Google API. Any
real Google-side write requires a write scope and mutates the user's live
Google account state — therefore it is `EXTERNAL_WRITE`, gated by approval,
implemented exclusively in 8E. The bright line: **the boundary is "does
this call a Google mutating endpoint," never "is the result immediately
visible to a third party."**

### AD-W7-06 `app/intake/`, not `app/whatsapp/`

**Decision:** 8F's package is `app/intake/`, generic over `source_channel`.
V1 implements exactly one channel value, `whatsapp_manual`, populated only
by `/wa`.

### AD-W7-07 `/wa` never talks to WhatsApp, permanently

**Decision:** no WhatsApp API, no WhatsApp Web automation, no outbound path
back to WhatsApp of any kind, ever.

### AD-W7-08 `PendingInteraction` is new, minimal, additive persistence

**Decision:** new `conversation_pending_interactions` table, owned
exclusively by `app/conversation/schema.py`. `app.memory.models.WorkSession`
was evaluated and rejected. State machine: `open -> resolved | expired |
superseded`, at most one `open` row per `chat_id` (database-enforced
partial unique index).

### AD-W7-09 Contextual confirmation never resolves a HIGH-risk choice — only exact numbered/labeled selection does

**Decision:** `PendingInteraction.max_risk_level` is computed at creation
time. Contextual synonyms resolve a pending interaction only when
`max_risk_level == 'low'`. Against a `high`-risk interaction, every
contextual synonym is `ambiguous` (re-ask); only an exact numbered or
exact-label reply resolves it.

### AD-W7-10 Parallel-stage shared-bootstrap ownership — the stage integration branch (G1 for Stage 1, G3 for Stage 3) owns `build_application()` composition and `app/main.py` construction; feature branches do not restructure shared bootstrap **(revised twice — see both revision notes below)**

**Original decision (superseded):** 8A/8F/8G would each independently append
one new optional parameter to `build_application()`'s signature and its
`app/main.py` call site, in a fixed order, each branch rebasing onto the
integration branch immediately before merging.

**Control Tower finding (first revision):** a fixed append order still
requires feature branches to sequence their commits around a shared,
growing function signature and its `app/main.py` call site — real
isolation for parallel Git work means none of the branches touches that
signature at all, not merely that they touch it in an agreed order. This
was not sufficient isolation, for Stage 1.

**Control Tower finding (this pass, second revision):** the same isolation
principle was initially applied to Stage 1 (8A/8F/8G) only, on the reasoning
that Stage 3 (8C/8D) merges sequentially onto an already-integrated base and
so was assumed lower-risk. Control Tower's review determined that reasoning
was incomplete: **8C and 8D are still two independently-developed,
genuinely parallel feature branches**, and if both were left free to
independently edit `build_application()`'s signature and `app/main.py` as
their own integration strategy, they would face exactly the same
shared-signature collision Stage 1 already had to solve — merely arriving
at it one stage later does not make it safe. The same principle now applies
uniformly to **every stage with more than one parallel feature branch**.

**Revised decision, generalized to any stage with parallel feature
branches (Stage 1: 8A/8F/8G → G1; Stage 3: 8C/8D → G3):**

1. **Each feature branch owns its package and its own tests in complete
   isolation.** Neither `app/main.py` nor `build_application()`'s function
   signature is edited by any parallel feature branch, in any stage.
2. Each sprint's Telegram-facing behavior is written as a plain,
   independently unit-testable handler function in its own new module
   (`app/google_workspace/telegram.py`, `app/intake/telegram.py`,
   `app/drafting/telegram.py`, `app/workspace_bridge/telegram.py`) that
   reads its service via `context.bot_data.get("<key>")` and degrades
   safely (a clear "unavailable" reply) if that key is absent — so the
   handler is fully testable today by constructing a fake `bot_data` dict
   directly, with no dependency on `build_application()` ever being
   touched.
3. **A feature branch may optionally add its own single, isolated
   `application.add_handler(CommandHandler(...))` line** for its own new
   command(s) if end-to-end branch-level testability against a real
   `Application` object is wanted — a minimal, non-overlapping append, the
   same low-collision pattern Wave 6 already proved safe across five
   parallel sprints. It must never be paired with editing the shared
   function signature, `app/main.py`, or any `bot_data` assignment.
4. **The relevant stage's integration branch — not any single feature
   sprint — owns:** constructing the real service objects in `app/main.py`;
   adding the corresponding new parameters to `build_application()`'s
   signature in one coordinated edit; setting the corresponding
   `application.bot_data[...]` assignments; ensuring every new command is
   registered exactly once (whether a feature branch already added its own
   line per point 3, or the integration branch adds it fresh); and
   validating final handler ordering (§11). For Stage 1 this is the **G1**
   branch (`WorkspaceConnectorBundle`/`IntakeService`/`ConversationService`).
   For Stage 3 this is the **G3** branch (`DraftingService`/
   `WorkspaceBridgeService`).
5. **Generic text-handler behavior remains 8G-owned, unchanged.**
   `handle_text`'s body edit (the three-line pending-interaction check) is
   still written and merged directly by 8G's own branch — it is a narrow,
   self-contained edit with zero collision risk (no other sprint, in any
   stage, touches `handle_text`), so deferring it to any integration branch
   would add coordination cost for no safety benefit.
6. Each stage's integration pass is a formally gated, tested integration
   pass, not an informal merge — see §11's full mechanism and
   `tests/test_wave7_stage1_integration.py`/
   `tests/test_wave7_stage3_integration.py`, both modeled directly on
   Wave 6's own `tests/test_wave6_integration.py` precedent (§0).

**Scope note:** this principle applies to **Stage 1 and Stage 3** — the two
stages with genuinely parallel feature branches. Stage 2 (8B, solo) and
Stage 4 (8E, solo) were never at risk of this collision, since a single
sprint appending its own parameter to an already-stable signature has no
concurrent branch to collide with; both continue to append their own
`build_application()` parameter directly, unchanged.

### AD-W7-11 `app/config.py`/`.env.example` need zero Stage 1–3 edits

**Decision:** 8A/8B/8C/8D/8F/8G introduce no new environment variable — 8A
reuses the already-present, already-optional Sprint 5C settings verbatim (no
Keep-related flag exists — Keep is fully out of scope, §6, so there is
nothing to opt into); 8F/8G need only the existing `NOVA_MEMORY_DB_PATH`.
Only 8E adds a setting whose value changes runtime behavior (a kill switch
gating whether approved external writes may actually execute).

### AD-W7-12 Cost control — no mandatory paid/LLM dependency; bounded, non-polling API usage

**Decision:** every deterministic capability has a working, fully
deterministic Python code path with no LLM call in it. Optional LLM
enrichment is always an injected, `None`-safe dependency. No Wave 7 sprint
adds a background polling loop against any Google API. Every list/search
method has an explicit, bounded result limit. Standard (non-paid) Google
Workspace API quota tiers are assumed sufficient for single-user NOVA
operation; if implementation discovers a capability requires billing or
special licensing, that capability must report itself limited/unavailable
and require an explicit human decision before any cost is incurred.

### AD-W7-13 Contacts deferred, documented only, non-blocking

**Decision:** no `ContactsService` implementation ships in Wave 7. A
`Protocol` signature is documented in `docs/SPRINT_8A.md` §7 for a possible
future sprint. Justification: zero sprint in the frozen 8A–8G set has a
user-facing feature that consumes contact data; requesting a People API
scope with no consuming feature fails least-privilege for no functional
gain. Contacts must never become a Wave 7 blocker under any circumstance —
if a future repository change makes a read-only lookup seam clearly
justified without adding unnecessary scope/dependency, it is still a
separate, explicitly-scoped decision, not an automatic Wave 7 addition.

### AD-W7-14 Google Keep — HOLD, deferred, out of active Wave 7 scope **(revised this pass — supersedes the previous "capability-probed, optional" design)**

**Decision:** Google Keep is **not part of the active Wave 7 implementation
scope**, by explicit Control Tower directive. This supersedes the
originally-frozen design (a capability-probed, opt-in, safely-degrading
Keep integration). That design is not wrong on its own terms, but Control
Tower has decided the underlying access-model uncertainty (§0: the public
Keep API's intended access model is Google Workspace domain/service-account
delegation, not the personal end-user OAuth consent flow this repository
implements) is reason enough to hold the entire capability rather than ship
a probe-and-degrade shim for it this wave.

**Concretely, for Wave 7 as frozen:**

- Sprint 8A does **not** implement Google Keep in any form — no package, no
  probe method, no capability check, no `KeepService`.
- No Keep OAuth scope (`keep.readonly` or otherwise) is ever requested by
  any Wave 7 sprint, including under any opt-in configuration flag — the
  opt-in flag itself has been removed, not merely defaulted off.
  `WorkspaceCapabilityReport` (§10) does not include a Keep entry at all.
- Sprint 8C does **not** offer a Keep-note content type.
- Sprint 8E does **not** implement any Keep write action, and
  `WorkspaceActionType`'s vocabulary contains no Keep-related value.
- Keep is not part of any Wave 7 acceptance criterion, integration gate, or
  risk-matrix row that implies it is an active, working, or even
  gracefully-degrading capability.
- Keep is not a required Workspace service for Wave 7 completion in any
  sense — its absence is by design, not a gap to be reported as unavailable
  at runtime.
- No unofficial Keep API, browser scraping, or UI automation may ever
  substitute for official API access — this prohibition is permanent and
  does not depend on Keep's active/deferred status.

**Conditions for revisiting Keep in a future wave** (all four required,
per Control Tower's explicit direction):

1. Verification against current official Google API documentation of
   Keep's actual access model and eligibility for the account types NOVA
   targets.
2. Concrete account-eligibility verification (not assumed from this
   document or any prior one).
3. An affirmative case that the API is suitable for NOVA's personal/
   executive-assistant use case, not merely technically reachable.
4. A separate, explicitly-scoped architecture decision — Keep does not
   re-enter scope as a side effect of any other Wave 7+ change.

### AD-W7-15 Provider independence — connector execution never depends on an LLM/provider

**Decision:** every Google API call in `app/google_workspace/**` executes
with zero dependency on `app/providers/**` or any configured LLM. If
`ProviderGatewayService` is entirely absent, Gmail search, Calendar list,
Drive search, and Docs/Sheets/Slides reads all continue to function
exactly as if it were present. LLMs may be injected as optional enrichment
above the connector, never as a requirement for raw API access.

### AD-W7-16 Multi-model routing — Gemini is a reasoning role, never the OAuth/credential owner

**Decision:** the Google Workspace OAuth credential, token storage, and API
client construction belong exclusively to `app/google_workspace/**` — never
to a provider adapter, and never conditionally routed through "Gemini" as
if Gemini held Google credentials. Gemini's role is reasoning *about*
Workspace content NOVA's connector has already read safely — a consumer of
DTOs like any other optional LLM enrichment point, never a distinct
authentication path.

### AD-W7-17 Workspace provenance identity — stable external identity first, content fingerprint secondary **(revised this pass — corrects `account_namespace`'s source)**

**Decision:** neither 8D's `WorkspaceSourceRef` nor 8F's
`ExternalMessageIntake` uses a content-derived hash as its primary identity
or its sole deduplication key. Two distinct Workspace objects (two
different emails, two different files) that happen to contain identical or
near-identical text must never be collapsed into one candidate merely
because their content matches.

- **8D (`WorkspaceSourceRef`):** primary identity is
  `(source_system, account_namespace, external_source_type,
  external_source_id)` — the real Gmail message/thread ID, Calendar event
  ID, or Drive/Docs/Sheets/Slides file ID, scoped to which configured
  Google **account** it came from. This tuple is enforced as a
  database-level unique index — the actual deduplication/idempotency
  mechanism. A `content_fingerprint` field is retained, but purely as a
  secondary, non-unique hint (e.g., "this candidate's text looks similar to
  another candidate" for UI purposes) — it never gates identity.

  **`account_namespace`'s source, corrected this pass:** the prior draft of
  this decision reused `GoogleAuthenticator`'s existing `client_id_hash` for
  `account_namespace`. That is wrong and has been corrected.
  `client_id_hash` identifies the **OAuth application/client** (the
  `installed.client_id` in the configured client-secrets file) — it is the
  same value for every Google account a user ever authorizes through that
  one NOVA Desktop Client. It does **not** identify *which Google account*
  granted consent. **OAuth application identity is not Google account
  identity**, and conflating them would mean two different Google accounts
  authorized through the same NOVA installation collide into one
  `account_namespace` — silently merging one person's Gmail/Calendar/Drive
  provenance with another's.

  The frozen requirement instead: `account_namespace` must be derived from
  a validated, stable identifier of the **authenticated Google account
  itself** — never the OAuth client. Repository evidence today
  (`app/google_workspace/auth.py`) exposes no such account-level identifier
  anywhere — `GoogleAuthenticator` currently validates and hashes only
  client identity and scopes, never account identity. This is therefore a
  **new, additive 8A responsibility** (§7 of `docs/SPRINT_8A.md`), not
  something 8D may invent independently: 8A adds one new method,
  `GoogleAuthenticator.get_account_namespace() -> str | None`, following
  the exact discipline already used for `client_id_hash` — validated first
  (reusing `_validate_credentials()`'s existing identity/scope check
  pattern), then hashed (same sha256-truncated convention), returning
  `None` when not connected. The raw account identifier (email, subject ID,
  or whatever underlying value is used) is never returned, logged, stored,
  or exposed by this method or by any caller — only the resulting opaque
  hash ever leaves `GoogleAuthenticator`.

  **The exact underlying value this method hashes is left to
  implementation-time verification**, per this task's own instruction not
  to invent an implementation field repository evidence cannot currently
  support: the granted scopes already include `userinfo.email`/
  `userinfo.profile` (Sprint 5C's `ScopeBundle.DEFAULT`), so a validated,
  authenticated-account-scoped identifier is expected to be obtainable
  through the existing credentials (e.g., a validated ID token subject
  claim, or an authenticated call against the userinfo endpoint using the
  already-established credentials) — but *which* mechanism the
  `google-auth`/`google-auth-oauthlib` library versions this repository
  pins actually make available must be confirmed against current library
  behavior and Google's own OpenID Connect documentation before 8A
  implements this method, not assumed from this document. What is frozen,
  not deferred, is the **semantic contract**: stable per-account, distinct
  across accounts, never the OAuth client identity, never a raw
  email/subject value once returned.

  `client_id_hash` remains useful, unchanged, for OAuth-client/config
  diagnostics (e.g. confirming `/workspacestatus` is pointed at the
  expected client-secrets file) — it is simply never used as
  `account_namespace` going forward.
- **8F (`ExternalMessageIntake`):** WhatsApp gives NOVA no stable external
  message identity at all — manual copy/forward is the only channel, by
  design (AD-W7-07). Identity is therefore two-tier: (1) the canonical
  local identity is simply the row's own primary key — NOVA does not
  pretend to know a WhatsApp-native ID; (2) where Telegram's own delivery
  identity (`telegram_update_id`) is available, it is used as a **permanent**
  idempotency key — a genuine duplicate delivery of the same Telegram
  update must always resolve to the same intake row; (3) a
  `content_fingerprint` is used only as a **scoped, time-bounded** hint (a
  short window, e.g. a few minutes) to catch an accidental double-paste —
  never a permanent constraint, since two genuinely distinct WhatsApp
  messages sent hours or days apart may legitimately contain identical
  short text (e.g. "ok", "yes noted") and must not be silently merged.

**Alternative considered:** keep the original content-hash-unique design for
both. **Rejected:** it fails the concrete case Control Tower flagged — two
different emails, or two different manually-forwarded messages, with
identical or near-identical text would be silently treated as "the same
Workspace object," which is a correctness defect for provenance (the whole
point of `WorkspaceSourceRef`/`ExternalMessageIntake` is to know *which*
real object a candidate came from) and a duplicate-suppression risk for
`/wa` specifically (legitimately repeated short messages would be
permanently dropped after the first).

---

## 5. Active Google Workspace service matrix and OAuth scope policy

### 5a. Active service matrix (frozen, explicit)

The active Wave 7 Google Workspace scope is **exactly six services**. Keep
is deferred (§6); Contacts is deferred/documented-only (AD-W7-13); neither
appears in this table because neither is part of active Wave 7 scope.

| Service | Existing vs. new | READ capability | WRITE capability | Sprint (READ) | Sprint (WRITE) | Approval required (WRITE) | Canonical source identity | Failure/fallback behavior |
|---|---|---|---|---|---|---|---|---|
| Gmail | New | Search messages, read bounded metadata/snippet, list thread | Create draft, send, reply | 8A | 8E | Yes | `(gmail, account_namespace, message\|thread, external_id)` | Typed `authentication`/`permission`/`not_found`/`rate_limit`/`network` error; no crash, no retry loop |
| Google Calendar | Existing (5D), reused | List/search events, free/busy, conflict detection | Create event, update event | 8A (reused) | 8E | Yes | `(calendar, account_namespace, event, external_id)` | Same typed-error taxonomy, unchanged from 5D |
| Google Drive | Existing (5E), reused | Search/list metadata within allowlisted folders, export PDF/XLSX | Share file (permission grant only — no content edit) | 8A (reused) | 8E | Yes | `(drive, account_namespace, file, external_id)` | Same typed-error taxonomy, unchanged from 5E; empty result if no folder allowlisted |
| Google Docs | New | Read document structure/text (bounded paragraph walk) | Create document, edit document | 8A | 8E | Yes | `(drive, account_namespace, file, external_id)` — Docs files are Drive files | Typed not-found/permission error; doc must be Drive-discoverable first |
| Google Sheets | New | Read spreadsheet metadata, read a bounded explicit A1 range | Write a bounded range | 8A | 8E | Yes | `(drive, account_namespace, file, external_id)` | Same; malformed/unsafe A1 range rejected before any API call |
| Google Slides | New | Read presentation structure/text (bounded per-slide extraction) | Create presentation, update presentation | 8A | 8E | Yes | `(drive, account_namespace, file, external_id)` | Same |

**Frozen rule:** Sprint 8A is READ ONLY across all six active services — no
write scope is requested, referenced, or required by 8A under any
configuration. Sprint 8E is the sole introducer of every write capability
in this table, always behind explicit approval (§7 risk matrix).

### 5b. Scope classes and detailed OAuth policy

Exact scope strings are verified against current Google API documentation
at implementation time, not frozen here from memory:

| Capability | Google API | READ scope class | WRITE scope class | Eligibility limitation | Fallback behavior |
|---|---|---|---|---|---|
| Search/read Gmail messages | Gmail API | `gmail.readonly` | — | None known | N/A |
| Search/read Calendar events, free/busy, conflicts | Calendar API | `calendar.readonly` (existing) | — | None known | N/A |
| Search/read Drive metadata, export PDF/XLSX | Drive API | `drive.readonly` (existing) | — | Folder allowlist required (existing 5E constraint) | Empty result if no folder allowlisted |
| Read Docs structure/text | Docs API | `documents.readonly` | — | Doc must be Drive-discoverable first | Typed not-found/permission error |
| Read Sheets metadata/bounded range | Sheets API | `spreadsheets.readonly` | — | Same | Same |
| Read Slides structure/text | Slides API | `presentations.readonly` | — | Same | Same |
| Contacts/People lookup | People API | (documented only, not requested) | — | N/A | `ContactsService` Protocol documented, no call site |
| Create real Gmail draft / send / reply | Gmail API | — | `gmail.compose` (verify narrowest sufficient scope at implementation time) | None known | Fails closed, `WorkspaceAction` marked `failed`, no auto-retry |
| Create/update Calendar event | Calendar API | — | `calendar.events` | None known | Same |
| Share a Drive file | Drive API | — | `drive.file` (narrowest scope covering files the connector already touches) | Target file must already be within an allowlisted folder | Same |
| Create/edit a Docs file | Docs API | — | `documents` (write) | None known | Same |
| Write a Sheets range | Sheets API | — | `spreadsheets` (write) | None known | Same |
| Create/update a Slides presentation | Slides API | — | `presentations` (write) | None known | Same |
| Delete email/event/file (any service) | any | — | — | **Not implemented in Wave 7 — no sprint ships any delete capability** | N/A |

**Deferred, not part of the active matrix above:** Google Keep — see §6 and
AD-W7-14 for the full deferral record. No scope, read, or write capability
for Keep exists anywhere in active Wave 7 scope.

**Hard constraints (frozen, unchanged from the brief):**

- No write scope is requested by 8A/8B/8C/8D/8F/8G under any configuration.
- No service-account flow anywhere — every Google API in this document is
  reached only through the existing personal-user Desktop-App OAuth
  credential.
- No secret, token, or credential value is ever committed, logged, or sent
  to Telegram — enforced today by `SecureFileTokenStorage` +
  `get_connection_status()`'s boolean/hash-only contract.
- `.env.example` documents variable names and safe placeholder/false values
  only, never a real client secret, token path value, or scope string tied
  to a specific real account.

---

## 6. Google Keep — deferred (full record)

**Google Keep is deferred and out of active Wave 7 scope.**

This section exists to record *why*, so the decision is traceable rather
than a silent omission, and so a future wave revisiting Keep has the actual
reasoning available rather than needing to re-derive it.

The original architecture pass proposed treating Keep as capability-probed
and optional: an opt-in scope request, one bounded and cached probe call,
safe degradation on any failure, never blocking 8A's completion. That design
was internally consistent, but Control Tower's review determined the
underlying uncertainty was reason enough to hold the whole capability rather
than ship a probe-and-degrade shim this wave:

1. **Access model mismatch.** The other six active Google surfaces in this
   document are all reachable through a standard end-user OAuth consent
   flow against a personal Google account — exactly what
   `GoogleAuthenticator` already implements. Google's Keep API, as publicly
   documented at the time this architecture was written, targets Google
   Workspace (managed/enterprise domain) accounts and is commonly reached
   via service-account domain-wide delegation, not personal-account
   end-user consent — a model NOVA's single-user Desktop-App credential
   was not primarily built for.
2. **No account-type detection exists today.** NOVA has no reliable, safe
   way to know in advance whether a configured Google account is a
   personal consumer account or a Workspace-managed account.
3. **The brief explicitly forbids any substitute** — no unofficial Keep
   API, no browser automation, no scraping, ever, independent of Keep's
   active/deferred status.

**Frozen wording, used consistently across every Wave 7 document:** *"Google
Keep is deferred and out of active Wave 7 scope."* No document in this wave
should describe Keep as "optional," "capability-probed," or "enabled if
supported" — those phrases describe the superseded design, not the current
one.

**What this means concretely** (restated from AD-W7-14 for a single,
complete reference point):

- No `app/google_workspace/keep/` package exists.
- No Keep scope is requested, ever, by any sprint, under any configuration.
- No opt-in flag exists in `app/config.py`/`.env.example` for Keep.
- `WorkspaceCapabilityReport` (§10) has no Keep entry.
- 8C has no `keep_note` content type.
- 8E has no Keep action type and no Keep write service.
- No Wave 7 test, acceptance criterion, or integration gate references Keep
  as an active, tested, or gracefully-degrading capability.

**Revisit conditions:** see AD-W7-14 — verification against current Google
documentation, account-eligibility verification, an affirmative use-case
suitability case, and a separate architecture decision, all four required.

---

## 7. Security classification (action-risk matrix) — active Workspace surface

| Action | Class | Confirmation required |
|---|---|---|
| Read Gmail | `SAFE_READ` | None |
| Search Gmail | `SAFE_READ` | None |
| Read Calendar | `SAFE_READ` | None |
| Search Drive | `SAFE_READ` | None |
| Read Docs | `SAFE_READ` | None |
| Read Sheets | `SAFE_READ` | None |
| Read Slides | `SAFE_READ` | None |
| Create WorkItem candidate | `SAFE_INTERNAL_WRITE` | None |
| Create canonical WorkItem (commit) | `SAFE_INTERNAL_WRITE` | None — existing domain rule applies |
| Add KnowledgeItem (commit) | `SAFE_INTERNAL_WRITE` | None — existing domain rule applies |
| Prepare email content | `SAFE_INTERNAL_WRITE` | None |
| Prepare document (Docs) content | `SAFE_INTERNAL_WRITE` | None |
| Prepare spreadsheet change | `SAFE_INTERNAL_WRITE` | None |
| Prepare slide deck content | `SAFE_INTERNAL_WRITE` | None |
| Create Gmail remote draft | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Send email | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Reply email | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Create Calendar event | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Update Calendar event | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Create Docs file | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Edit Docs | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Write Sheets | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Modify Slides | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Share Drive file | `EXTERNAL_WRITE` | Explicit numbered confirmation |
| Delete Gmail message | `DESTRUCTIVE` | **Not implemented in Wave 7 — no sprint ships this** |
| Delete Drive file | `DESTRUCTIVE` | **Not implemented in Wave 7** |
| Delete Calendar event | `DESTRUCTIVE` | **Not implemented in Wave 7** |
| Commit | `PRIVILEGED` | Existing Git safety rules, unchanged (never contextual) |
| Push | `PRIVILEGED` | Existing Git safety rules, unchanged (never contextual) |
| Merge | `PRIVILEGED` | Existing Git safety rules, unchanged (never contextual) |

Google Keep has no row in this matrix — it is not an active Wave 7 action
of any risk class (§6). Wave 7 does not weaken any existing Git safety rule
and introduces no path by which a contextual reply resolves a `PRIVILEGED`
or `DESTRUCTIVE`-class action.

**Safe autonomous actions (no approval required, subject to existing
security policy):** READ, ANALYZE, CLASSIFY, SUMMARIZE, DRAFT, PREPARE,
RECOMMEND, TEST, QUEUE SAFE INTERNAL WORK.

---

## 8. Data minimization policy

- Metadata first: every list/search operation returns bounded metadata
  before any full-content read.
- No default bulk ingestion: Gmail is never scanned wholesale; Drive is
  never mirrored; every read is scoped to an explicit query/folder/range/
  document requested by name or ID.
- Full body/content is retrieved only when a specific capability genuinely
  needs it and is never persisted beyond what that capability's own
  bounded record requires.
- Knowledge ingestion (8D → `KnowledgeItem`) is always an intentional,
  explicit commit step — never automatic.
- External source references (`external_source_id` — Gmail message ID,
  Drive/Docs/Sheets/Slides file ID, Calendar event ID) are preserved for
  provenance; the raw content behind them is not duplicated beyond what a
  specific, already-reviewed record legitimately needs to say (§4, AD-W7-17).

---

## 9. Failure model (frozen)

| Condition | Required behavior |
|---|---|
| No OAuth configured | NOVA still boots; every Workspace read/status call reports `unavailable`/`not_configured`; no network call attempted. |
| Gmail unavailable/erroring | Calendar/Drive/Docs/Sheets/Slides continue working independently — each service's failure is isolated, never cascading. |
| Provider/LLM unavailable | Deterministic Workspace reads (8A/8B core) continue unaffected (AD-W7-15). |
| Expired OAuth token | Safe, typed `authentication` category error; no token/secret ever logged. |
| Network failure | Bounded failure (existing typed `network`/`provider_failure` categories); no unbounded retry loop. |
| Ambiguous external-write result (8E) | No blind automatic retry; `WorkspaceAction` is left in a state requiring explicit human reconciliation, never silently marked `succeeded`. |
| Ambiguous Telegram confirmation (8G) | Re-ask; never guess, never silently drop. |
| Duplicate Telegram message delivery | Idempotent at every layer that matters — 8E's dispatch/action idempotency key, 8F's `telegram_update_id` key, 8G's compare-and-swap resolution. |

Google Keep has no row here — it is not an active capability whose failure
mode Wave 7 needs to define (§6).

---

## 10. `WorkspaceCapabilityReport` — the one status-reporting seam every active service feeds

Owned by 8A (`app/google_workspace/status.py`), read by `/workspacestatus`:

```python
@dataclass(frozen=True)
class ServiceCapabilityStatus:
    service: str        # 'gmail'|'calendar'|'drive'|'docs'|'sheets'|'slides'|'contacts'
    available: bool
    reason: str | None  # e.g. 'not_configured'|'scope_not_granted'|'not_implemented'|'ok'

@dataclass(frozen=True)
class WorkspaceCapabilityReport:
    generated_at: str
    connection: "WorkspaceConnectionStatus"  # existing auth-level status (5C)
    services: tuple[ServiceCapabilityStatus, ...]
```

`services` always contains exactly **seven** entries — the six active
services plus `contacts` (always `available=False, reason='not_implemented'`
per AD-W7-13). **There is no `keep` entry, under any configuration** — Keep
is not a service the connector reports on, not even as "deferred" or
"unavailable," because it is not part of the connector's active surface at
all (§6). See `docs/SPRINT_8A.md` §8 for the full construction contract.

---

## 11. Telegram / `app/main.py` ownership and parallel-stage integration mechanism (Stage 1 and Stage 3)

**Command handler ownership (new commands only; existing commands listed in
§0 remain untouched):**

| Command | Owner sprint | Stage | Registration |
|---|---|---|---|
| `/workspacestatus` | 8A | 1 | May be added by 8A's own branch (isolated, single-line) or by G1; deduplicated at G1 either way |
| `/wa <text>` | 8F | 1 | Same |
| (no new command — 8G augments the existing generic text handler only) | 8G | 1 | N/A |
| `/inbox`, `/agenda` | 8B | 2 | 8B's own branch (Stage 2 is solo, no collision) |
| `/draftreply`, `/draftmemo`, `/drafts`, `/draft`, `/draftsheet`, `/draftslides` | 8C | 3 | May be added by 8C's own branch (isolated, single-line each) or by G3; deduplicated at G3 either way |
| `/workspacecandidates`, `/workspacecommit` | 8D | 3 | Same — 8D's own branch or G3 |
| `/workspaceaction <id>`, `/workspaceactions` (plus reuse of the existing `/approve`/`/reject`) | 8E | 4 | 8E's own branch (Stage 4 is solo) |

**Handler-function ownership:** each sprint's Telegram-facing code lives in
its own new module (e.g. `app/intake/telegram.py`, `app/drafting/
telegram.py`, `app/workspace_bridge/telegram.py`); `telegram_bot.py` only
imports and registers — no sprint writes business logic inline there. Every
Stage 1 and Stage 3 handler function reads its service via
`context.bot_data.get("<key>")` and degrades to a clear "unavailable" reply
if absent, so it is independently testable without `build_application()`
ever being touched (AD-W7-10).

**`build_application()` composition — Stage 1 is G1-owned, Stage 3 is
G3-owned (AD-W7-10, generalized this pass):** none of 8A/8F/8G, and none of
8C/8D, edits `build_application()`'s signature or `app/main.py`'s
construction/call site. Each stage's own integration branch performs, in
one coordinated pass, after that stage's feature branches' package-level
tests and gates pass independently:

**G1 (Stage 1 — 8A, 8F, 8G):**

1. Construct `WorkspaceConnectorBundle`, `IntakeService`,
   `ConversationService` in `app/main.py`, each `None`-safe, in that order.
2. Add all three corresponding parameters to `build_application()`'s
   signature in one commit.
3. Set `application.bot_data["google_workspace"]`, `["intake"]`,
   `["conversation"]`.
4. Ensure `/workspacestatus` and `/wa` are each registered exactly once
   (reusing a feature branch's own line if it added one, or adding it
   fresh).
5. **Validate final handler ordering** — explicit commands, then existing
   dedicated handlers, then (inside `handle_text`) the pending-interaction
   resolver, then the generic natural-language fallback — matches the
   actual registration order in `build_application()` (commands registered
   first; `MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)`
   registered last, already guaranteeing commands take precedence — 8G's
   `handle_text` edit only adds a decision *inside* that already-last
   handler, never changing registration order).
6. Add `tests/test_wave7_stage1_integration.py`, modeled directly on
   `tests/test_wave6_integration.py` (§0): real (non-stub)
   `WorkspaceConnectorBundle` + `IntakeService` + `ConversationService`
   wiring, `build_application()` carrying all three, combined idempotent
   initialization against one temporary SQLite database, and an assertion
   that every Stage 1 command is registered exactly once.

**G3 (Stage 3 — 8C, 8D), the same mechanism applied one stage later:**

1. Construct `DraftingService` and `WorkspaceBridgeService` in
   `app/main.py`, each `None`-safe, appended after Stage 1/2's already-
   merged construction blocks.
2. Add both corresponding parameters (`drafting`, `workspace_bridge`) to
   `build_application()`'s signature in one commit.
3. Set `application.bot_data["drafting"]`, `["workspace_bridge"]`.
4. Ensure `/draftreply`, `/draftmemo`, `/drafts`, `/draft`, `/draftsheet`,
   `/draftslides`, `/workspacecandidates`, and `/workspacecommit` are each
   registered exactly once (reusing either feature branch's own lines if
   added, or adding them fresh).
5. **Validate final handler ordering**, unchanged from G1's rule — Stage 3
   adds no new interaction with `handle_text` or the pending-interaction
   resolver, so this step confirms nothing regressed, not that anything new
   needs sequencing.
6. Add `tests/test_wave7_stage3_integration.py`, the same shape as
   `tests/test_wave7_stage1_integration.py`: real `DraftingService` +
   `WorkspaceBridgeService` wiring, `build_application()` carrying both,
   combined idempotent initialization, and an assertion that every Stage 3
   command is registered exactly once.

Neither pass is attributed to any single feature sprint individually — each
is the relevant gate's own deliverable, performed once that stage's feature
branches are independently ready, by whoever runs that stage's integration
under the existing operating model (Control Tower directs; Codex/Claude
perform and review).

**Generic text handler ordering (8G is the only sprint editing `handle_text`'s body, in any stage, done directly, not deferred — AD-W7-10 point 5):**

```python
conversation = context.bot_data.get("conversation")
resolution = conversation.try_resolve_pending(user_id, user_message) if conversation else None
if resolution is not None:
    await message.reply_text(resolution.response_text)
    return
```

Inserted before `parse_workspace_intent()` is called. If `bot_data`
has no `conversation` entry or no interaction is open, behavior is
byte-for-byte unchanged.

**`app/config.py` / `.env.example`:** no Stage 1–3 edits with observable
behavior change (AD-W7-11). 8E adds its own new block, isolated, at
Stage 4.

---

## 12. Integration gates — formal, per-stage

**G1 — Stage 1 Integration Gate** (8A + 8F + 8G → Stage 1 integration
branch): each sprint's own package-level tests pass in isolation, with zero
edits by any of the three to `app/main.py` or `build_application()`'s
signature; the G1 pass itself performs and tests the coordinated wiring
(§11); `tests/test_wave7_stage1_integration.py` passes; full regression
suite passes on the integration branch; `/workspacestatus`, `/wa`, and
every pre-existing Telegram command (§0's list) all still work correctly
against the same running bot; handler ordering is explicitly validated.

**G2 — Stage 2 Integration Gate** (8B → Stage 2 integration branch, built on
G1's output): 8B's tests pass against the *real*, merged `GmailService`/
`CalendarService`; `/inbox`/`/agenda` render correctly; full regression
passes; Stage 1's capability is unaffected.

**G3 — Stage 3 Integration Gate** (8C + 8D → Stage 3 integration branch,
built on G2's output): each sprint's own package-level tests pass in
isolation, with zero edits by either to `app/main.py` or
`build_application()`'s signature; the G3 pass itself performs and tests
the coordinated wiring (§11 — `DraftingService`/`WorkspaceBridgeService`
construction, the two new `build_application()` parameters added together,
`bot_data` wiring, Stage 3 command-registration deduplication, handler-
ordering re-validation); `tests/test_wave7_stage3_integration.py` passes;
both sprints' tests pass against real Stage 1/2 services; the structural
"8C never calls a Google write endpoint" test passes; the structural "8D
writes no raw SQL into Control Tower/Knowledge tables" test passes; the
stable-external-identity dedup test and the distinct-account test
(AD-W7-17) pass; full regression passes.

**G4 — Final Wave 7 Integration** (8E → final integration branch, built on
G3's output): all 8E acceptance criteria pass against real, merged
8A/8C/8D/8G; every existing `app/dispatch/**` test still passes unmodified
except 8E's own additive registry/adapter extension; full regression
passes; human/Control Tower acceptance review completed; **human approval
obtained before any commit/merge/push to `main`.**

No automatic integration at any gate, at any stage. No Wave 7 sprint
performs real Google OAuth authentication as part of its own test suite.
No gate references Google Keep in any capacity.

---

## 13. Resolved by this freeze (cumulative, including this revision pass)

- `GEMINI`/Workspace dispatch-adapter gap (Wave 6 AD-W6-06/§7) — resolved:
  8E closes it via `GoogleWorkspaceAgentAdapter` (AD-W7-04).
- Whether any real Google-side write (including a draft) is external —
  resolved: yes, always, across all active writable services (AD-W7-05).
- `app/intake/` vs `app/whatsapp/` — resolved: `app/intake/` (AD-W7-06).
- Where `PendingInteraction` lives — resolved: new, minimal, additive table
  (AD-W7-08).
- Whether contextual confirmation can ever authorize a high-risk action —
  resolved: never (AD-W7-09).
- **Stage 1 and Stage 3 `main.py`/`telegram_bot.py` shared bootstrap
  wiring — resolved: owned by each stage's own integration branch (G1 for
  8A/8F/8G, G3 for 8C/8D), not independently edited by any parallel feature
  branch in either stage (AD-W7-10, revised twice — first restricted to
  Stage 1, then extended to Stage 3 this pass).**
- **Whether Google Keep is active Wave 7 scope — resolved: no, fully
  deferred, out of active scope, pending a future separately-scoped
  decision (AD-W7-14, revised).**
- Whether Contacts ships — resolved: documented only, not implemented,
  non-blocking (AD-W7-13).
- 8C/8E responsibility overlap — resolved: content actions vs. structured
  actions split (§3.8).
- **Workspace provenance identity — resolved: stable external identity
  first for 8D, a two-tier Telegram-update/scoped-fingerprint model for 8F;
  content hashing alone is never the sole identity for either (AD-W7-17).**
- **`account_namespace`'s correct source — resolved this pass: the
  authenticated Google account's own identity, via a new 8A
  `GoogleAuthenticator.get_account_namespace()` method — never the OAuth
  client's `client_id_hash`, which the prior pass had mistakenly reused
  (AD-W7-17, corrected).**

No open questions remain for Control Tower on this pass; each sprint
document's own §14 records sprint-local, non-blocking technical debt.

---

## 14. Self-review (performed against this document and all seven sprint
documents, including this revision pass)

| Check | Finding | Resolution |
|---|---|---|
| Overlapping sprint ownership | None beyond the one flagged, justified exception | 8E's `app/dispatch/registry.py`/`adapters.py` edit remains explicitly named and scoped |
| Circular dependencies | 8C↔8E, 8D↔8E | Both resolved as inverted dependencies |
| Hidden write scope in 8A | None found | 8A's scope list (§5) is exhaustively read-only across all six active services |
| Raw Google client leakage into Telegram/domain logic | None found | Every Telegram handler and cross-package caller receives typed DTOs or service objects — never a raw `googleapiclient` `Resource` |
| Secret exposure paths | None found | Unchanged safe-status contract |
| Keep treated as guaranteed / optional / probed | **Found and fully corrected this pass** | Every Keep reference across all Wave 7 documents rewritten to "deferred, out of active Wave 7 scope" (§6, AD-W7-14); no package, scope, probe, DTO, test, or acceptance criterion for Keep remains anywhere |
| Content-hash-only deduplication | **Found and corrected this pass** | 8D now keys identity on stable external identifiers; 8F now uses a two-tier Telegram-update/scoped-fingerprint model (AD-W7-17) |
| Conflicting bootstrap ownership (8A/8F/8G) | **Found and corrected** | G1 now owns `build_application()` composition and `app/main.py` construction; feature branches touch neither (AD-W7-10, revised) |
| 8A/8F/8G collision risk | Reassessed and strengthened | Previously "frozen append order"; now zero shared-signature edits by any of the three (AD-W7-10) |
| Conflicting bootstrap ownership (8C/8D, Stage 3) | **Found and corrected this pass** | The G1-style isolation principle was initially scoped to Stage 1 only, leaving 8C/8D free to independently edit the shared signature in Stage 3 — the same collision class, one stage later. Corrected: G3 now owns Stage 3's `build_application()` composition and `app/main.py` construction the same way G1 owns Stage 1's (AD-W7-10, generalized) |
| `account_namespace` sourced from OAuth client identity instead of account identity | **Found and corrected this pass** | 8D no longer reuses `client_id_hash`; a new 8A method, `GoogleAuthenticator.get_account_namespace()`, derives identity from the authenticated account, never the OAuth client, with an explicit test guarding the two values are never equal (AD-W7-17, corrected) |
| 8C/8E responsibility overlap | None found | Content-action vs. structured-action split (§3.8) |
| 8D duplicate canonical state | None found | `commit()` always calls an existing service's existing public method |
| 8G weakening existing approval semantics | None found | `ApprovalService`/Git safety rules are untouched |
| Idempotency gaps | None found | Two independent layers in 8E; stable-identity unique index in 8D; two-tier identity in 8F |
| Double-send risk | None found | Compare-and-swap transitions throughout; no automatic retry anywhere in 8E |
| Provider dependency in core reads | None found | AD-W7-15 makes this explicit and testable |
| Contacts accidentally becoming mandatory | None found | AD-W7-13 explicit: documented only, never a blocker |
| Stage 1 requesting write scopes | None found | §5a/§5b confirm 8A/8F/8G request no write scope of any kind |
| Excessive new schema | Reviewed | Six new tables total across four sprints, each independently justified |
| Vague acceptance criteria | None found this pass | Every acceptance criterion is an observable, testable condition |

---

## 15. Architecture Freeze acceptance — complete only when every item below holds

- [x] All seven sprint contracts exist (`docs/SPRINT_8A.md`–`SPRINT_8G.md`).
- [x] This shared contract exists.
- [x] The repository's existing Workspace implementation was inspected
      before any new package was proposed.
- [x] Existing Workspace capability is documented (§0).
- [x] The dependency graph is explicit, with named stage gates (§2, §12).
- [x] Stage 1 parallelization is demonstrably safe on paper, **with feature
      branches verified to touch zero shared bootstrap files** (§2, §11,
      AD-W7-10).
- [x] Stage 3 parallelization is demonstrably safe on paper, **with the
      same zero-shared-bootstrap-file guarantee now extended to 8C/8D via
      G3, corrected this pass** (§2, §11, AD-W7-10).
- [x] Ownership/collision matrix exists and reflects G1-owned Stage 1 wiring
      and G3-owned Stage 3 wiring (§11, and each sprint's own §6/§7).
- [x] OAuth boundary exists (§0, AD-W7-01/02/03).
- [x] Permission strategy and the explicit active six-service matrix exist
      (§5a/§5b).
- [x] **Google Keep is explicitly deferred and out of active Wave 7 scope,
      consistently, everywhere** (§6, AD-W7-14) — verified by the
      grep-based consistency review accompanying the prior revision.
- [x] Read/write separation is explicit (§5, AD-W7-02/03).
- [x] Token/secret boundary is explicit (§0, §5).
- [x] 8B's fact/inference/recommendation intelligence boundary is explicit
      (`docs/SPRINT_8B.md` §0).
- [x] 8C's prepare-vs-write distinction is explicit (AD-W7-05, §3.8).
- [x] 8D's canonical-workflow-integration boundary, **with stable
      external-identity provenance correctly scoped to authenticated
      Google account identity, not OAuth client identity, corrected this
      pass**, is explicit (§3.7, AD-W7-17).
- [x] 8E's approval/idempotency contract is explicit (AD-W7-04, §5, §7).
- [x] 8F's `/wa` contract, with two-tier identity, is explicit (AD-W7-06/07,
      AD-W7-17).
- [x] 8G's confirmation semantics are explicit (AD-W7-08/09).
- [x] The risk matrix is explicit and covers the active Workspace surface
      (§7).
- [x] Test strategy is explicit at both the shared and per-sprint level,
      including the new `account_namespace`-source and Stage 3
      integration-ownership tests.
- [x] Every sprint has concrete, testable acceptance criteria.
- [x] Integration gates are explicit and named, including both G1's and
      G3's wiring-ownership responsibility (§12).
- [x] No implementation code was added during this pass.
- [x] No secret was accessed.
- [x] No OAuth authentication was performed.
- [x] No external Google API action was performed.
