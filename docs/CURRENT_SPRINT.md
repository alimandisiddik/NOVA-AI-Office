# CURRENT SPRINT

## Wave 1 — Completed

### Sprint 5A — Always-On Runtime

Status: Completed and merged

Key outcomes:

- macOS launchd background service;
- automatic startup and restart;
- single-instance protection;
- bounded logging;
- service status and health commands.

### Sprint 5C — Google Workspace Foundation

Status: Completed and merged

Key outcomes:

- secure Google OAuth desktop foundation;
- deterministic least-privilege scope governance;
- hardened local token storage;
- credential validation and local disconnect;
- approved Google client factory;
- no Calendar or Drive business operations yet.

### Sprint 6A.0 — Dissertation Workspace Foundation

Status: Completed and merging

Key outcomes:

- dissertation project, chapter, and subchapter registry;
- document-version metadata and lifecycle;
- paragraph maps;
- review jobs;
- append-only revision logs;
- metadata-only local storage.

## Next Sprint

### Sprint 5A.1 — Night Shift Runtime Foundation

Status: Implemented — see `docs/SPRINT_5A1.md` and `docs/night-shift-runtime.md`

Delivered scope:

- four persistent runtime modes (`active`, `night_shift`, `quiet`,
  `maintenance`), restart-safe, with `quiet`/`maintenance` sticky against
  the automatic scheduler;
- timezone-aware night-shift schedule (default `Asia/Jakarta`), safe
  midnight-crossing;
- persistent, classified overnight job queue (`approved_overnight`,
  `deferred_until_morning`, `critical_notify_only`, `prohibited`) as a hard
  allowlist — unregistered job types are always rejected;
- prepare → validate → save_draft → await_approval execution lifecycle for
  approved-overnight work only; no real executor yet;
- categorical, always-on prohibition of Git mutations, external messaging,
  Calendar mutations, file deletion/move, document overwrite, secret
  changes, purchases, and destructive migrations;
- fail-closed notification severity routing (informational / attention-
  required / critical) with one consolidated morning brief per day;
- built strictly on top of the existing Sprint 5A `run_singleton.py` /
  `scripts/service.sh` / `RotatingFileHandler` runtime — does not modify any
  of them;
- service interfaces reserved for Sprint 5B (Telegram commands:
  `/nightshift`, `/nightstatus`, `/nightqueue`, `/wake` — future contracts
  only, not implemented this sprint) and a future full-automation sprint
  (`register_job_executor()` hook).

Out of scope this sprint: any real overnight execution, Telegram commands,
Google Drive/Calendar operations, email/WhatsApp, document rewriting,
background token refresh, autonomous external communication, automatic Git
mutations.

## Wave 2 — In Implementation Review

- Sprint 5B — Executive Control Tower MVP — implemented locally; cooperative review and merge pending
- Sprint 5D — Google Calendar Integration
- Sprint 5E — Google Drive Read-Only

## Wave 3 — Completed

- Sprint 5B.1 — Agent Dispatch & Approval Operations — merged at `8c6f64a`
  — see `docs/WAVE_3_INTEGRATION_CONTRACT.md` and `docs/SPRINT_5B1.md`.
  Delivered: canonical `DispatchService`/`ApprovalService`, static
  `AgentRegistry` (eight agents), additive `dispatches`/`dispatch_attempts`/
  `approvals`/`approval_audit`/`dispatch_audit_log`/`dispatch_leases`
  schema, and Control Tower's `list_approvals()` extended with the
  dispatch/approval source.
- Sprint 5F — Full Night Shift Automation — merged at `749fa21`, rebased
  onto merged 5B.1 (`8c6f64a`) — see `docs/SPRINT_5F.md`. Delivered:
  `NightShiftWorker` (claim/execute/defer/recover/cancel) driven by
  `application.job_queue.run_repeating(...)`, consuming the real merged
  `app/dispatch/` with no local stand-in; every status change routes
  through `NightShiftService.transition_night_job()` and `JOB_TRANSITIONS`
  is unmodified from `main`; additive `night_queue_jobs` lease/dispatch/
  approval columns via a `PRAGMA table_info(...)`-guarded migration;
  `/nightshift`, `/nightstatus`, `/nightqueue` (with `cancel <job_id>`),
  `/wake`. Known limitation: approval-free automated completions rest at
  `draft_saved`, not `completed` — see `docs/SPRINT_5F.md`.

## Wave 4 — Sprint 6A Full Dissertation Workspace

Status: Completed and merged

Key outcomes:

- singleton dissertation workspace with chapter focus tracking;
- source, evidence, research-note, gap, audit, and linkage persistence;
- sourced and bounded academic evidence with additive SQLite migrations;
- Control Tower references for academic work items and decisions;
- read-only `/dissertation` Telegram workspace views.


## Wave 4.1 — Sprint 6B Dissertation Research & Evidence Workflow

Status: Implemented locally; independently reviewed, corrected, and given a
final corrective pass for evidence confidence. All Sprint 6B acceptance
criteria are complete — see `docs/SPRINT_6B.md`.

Finding: the source, evidence, gap, and multi-chapter-mapping data model,
repository, and service layer this sprint's spec calls for were already
delivered in Sprint 6A's merged `feat: add full dissertation workspace`
commit (`500664e`), including sensitive-content rejection and additive
migrations. An independent review found zero application-code changes had
actually been made under this sprint's branch prior to review — only
documentation asserted the work as newly done. See `docs/SPRINT_6B.md` for
the evidence-based acceptance matrix.

Genuinely new in this sprint: the `/dissertation` namespace was read-only.
The spec requires explicit write capability for adding a source and adding
evidence from Telegram; that was missing and has been added as
`/dissertation addsource <chapter n|-> | title | source type | citation |
locator` and `/dissertation addevidence <source id> | <chapter n|-> |
summary | locator`, using the same explicit pipe-delimited structured
syntax as `/task`, `/note`, and `/project` (no conversational parsing).
Both commands route through the existing `DissertationService.create_source`
/ `create_evidence` validation (source type, chapter/source existence,
length bounds, `SENSITIVE_CONTENT_PATTERN` rejection) and the existing
`_require_authorized_user` gate; no new tables or destructive migrations.
Read-only source/evidence views were extended to print record IDs
(additive) so a user can reference a source when adding evidence.

Final corrective pass: the accepted Sprint 6B specification also requires
evidence confidence and validation, which the review pass above had marked
missing rather than implementing. Added as a minimal, additive, backward-
compatible capability: `dissertation_evidence` gains a `confidence` column
(`LOW`/`MEDIUM`/`HIGH`, `NOT NULL DEFAULT 'MEDIUM'`, `CHECK`-validated),
delivered via the same `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info(...)`-
guarded `ALTER TABLE` pattern already used for `version_state` and
`current_focus` — a database that already has `dissertation_evidence`
without the column gets it added with the `MEDIUM` default on next
`DissertationService.initialize()`; a fresh database gets the column from
table creation. No destructive migration, no existing row invalidated.
`DissertationRepository.create_evidence` / `DissertationService.create_evidence`
accept an optional `confidence` keyword (default `"MEDIUM"`), normalize it
case-insensitively, and reject any value outside the enum with
`InvalidDissertationValueError`. `/dissertation addevidence` accepts
confidence as an optional trailing 5th pipe field (`<source id> | <chapter
n|-> | <summary> | <locator> | <confidence>`); omitting it uses the
documented `MEDIUM` default. Nine new tests cover valid enum values,
case-insensitive normalization, invalid-value rejection, persistence, the
additive migration against a simulated pre-existing table, and
backward-compatible defaulting for callers that omit confidence entirely.
Full regression: 644 passed (635 before this pass + 9 new).

## Wave 5 — Sprint 5G Multi-Provider Fallback Hardening

Status: Completed and merged

Key outcomes:

- `ProviderGatewayService` remains the only provider execution entry point.
- Coding and Architecture resolve deterministic configured-specialist-first
  chains with opaque 9Router combo aliases as fallbacks; Night Shift and
  Generic AI stay 9Router-combo-first with no specialist dependency.
- The provider layer allows at most three live attempts; availability and
  circuit skips are audited without consuming the live-attempt budget.
- Provider audits retain route/provider identifiers and response-reported model
  labels only; prompts, full responses, and credentials are never persisted.
- Dispatch and Night Shift remain existing seams — no second dispatch/approval
  system. Google Workspace mutations do not enter the generic provider
  fallback path.

Known limitation: Codex and Claude adapters are configuration-gated safe
stubs in this sprint. They execute no local CLI/shell command and always
report unavailable until a real executable/session is configured, so a
Coding/Architecture request safely falls back to the corresponding 9Router
combo today.

Known limitation: the Codex and Claude adapters are configuration-gated safe
stubs in this sprint and execute no CLI command or arbitrary shell input.

## Wave 5.1 — Sprint 5G.1 9Router Upstream Route Mapping Runtime Fix

Status: Implemented locally against `httpx.MockTransport`; **not yet
validated against a live 9Router deployment by an operator** — do not mark
this remediation complete until that live confirmation happens.

Root cause: Sprint 5G's `NineRouterAdapter` sent NOVA's own internal route
aliases (`nova-v1`, `nova-v1-coding`, `nova-v1-review`, ...) directly as the
`model` field to `POST /v1/chat/completions`. 9Router does not recognize
those aliases — confirmed via runtime evidence: `GET /v1/models` returns
9Router's real combo IDs (`general`, `Development`, `review`, `Fast`, ...),
and a direct `POST` with `model="general"` returns HTTP 200 with the actual
resolved model `gemini-pro-default`, while NOVA's `/ask` command (sending
`nova-v1`) returned HTTP 404.

Fix: an explicit, additive translation layer distinguishing three identities
that were previously conflated into one field:

1. **NOVA-internal route alias** (`nova-v1`, `nova-v1-coding`, `nova-v1-review`,
   ...) — stable, used throughout selection, fallback, and audit; never sent
   upstream.
2. **Upstream/provider route identity** (9Router's own combo ID — `general`,
   `Development`, `review`, `Fast`) — new `RegisteredModel.upstream_route_id`
   field (`app/providers/registry.py`), resolved per-request via
   `resolve_upstream_route_id()` (`app/providers/selection.py`), configurable
   per-alias override via `NOVA_PROVIDER_UPSTREAM_ROUTE_MAP`. This is what
   `NineRouterAdapter` now sends as `model`.
3. **Actual resolved model** (what 9Router itself reports it used, e.g.
   `gemini-pro-default`) — unchanged mechanism (`ProviderResponse.model_id`),
   now falls back to the upstream route rather than the internal alias when
   9Router's response omits `model`.

A NOVA alias routed through 9Router with no evidenced or configured upstream
mapping is excluded from the resolved provider chain at selection time — it
is never sent upstream as a literal internal alias (the exact defect this
sprint fixes), and `NineRouterAdapter` additionally refuses to make a network
call at all without a resolved upstream route, as defense in depth.
`nova-v1-coding-fallback` and `nova-v1-review-fallback` (introduced by
Sprint 5G, never evidenced against a real 9Router deployment) are left
unmapped by design rather than guessing; `nova-v1-fallback` (pre-existing
since Sprint 4A/4B) is a deliberate, documented exception mapped to the same
evidenced `general` combo as `nova-v1`, to preserve backward-compatible
fallback behavior rather than silently disabling long-standing behavior.

Base URL contract clarified and enforced:
`NOVA_PROVIDER_BASE_URL` must be the 9Router host root (e.g.
`http://localhost:20128`), never including a trailing `/v1` — the adapter
always appends `/v1/chat/completions` itself. `ProviderGatewayService`
now rejects a base URL ending in `/v1` at startup.

See `docs/SPRINT_5G1.md` for the full architecture decision, file-level
change list, and test evidence.

## Wave 5.3 — Sprint 5G.3 Night Shift PTB JobQueue Runtime Fix

Status: Implemented locally. Ready for testing.

Root cause: python-telegram-bot v22 `JobQueue.run_repeating` requires an async coroutine function, but `_nightshift_tick` was registered as a synchronous function.

Fix: Changed `_nightshift_tick` to an `async def` function while keeping its logic intact. Adjusted tests in `tests/test_telegram_nightshift.py` to `asyncio.run()` the coroutine manually when testing without a live event loop.


## Wave 5.4 — Sprint 5G.4 Safe Telegram Runtime Error Diagnostics

Status: Implementation under review — independently reviewed and corrected;
not yet merged.

Root cause: the global Telegram error handler swallowed the true exception
type and details, hiding the real root cause of failing Night Shift tasks
while discarding all context.

Fix: `handle_error` safely logs `context.error`'s type and a truncated
(500-char), control-character-neutralized message, while `update` is never
read or logged. An independent review of the first implementation found and
corrected several robustness/security defects before this could be marked
ready:

- the handler could itself raise (crashing the bot's last-resort error path)
  on a malformed `context` missing `.error` entirely, or on an exception
  whose own `__str__` raised — now wrapped so `handle_error` never
  propagates an exception under any input;
- redaction used three bare-substring keyword checks (`"token"`, `"secret"`,
  `"key"` via `in`), which both under- and over-matched: it missed
  Bearer-token/PEM-private-key/password/credential/`KEY=value` shapes
  entirely, while "key" alone false-positived on ordinary messages like
  "duplicate primary key". Redaction now reuses the codebase's shared,
  already-established `SENSITIVE_CONTENT_PATTERN`
  (`app/security.py`), supplemented with word-boundary `token`/`secret`
  matching (dropping bare `key`, which the shared pattern already covers
  more precisely via `api key`/`private key`);
- newline/control characters in an exception message were not neutralized,
  allowing attacker- or upstream-controlled content to forge additional
  fake-looking log lines — now stripped before logging.

See `tests/test_telegram_bot_error.py` (16 tests) for the full edge-case
matrix: malformed/missing context, missing/None error, unprintable `__str__`,
bare token/secret, API key, Bearer/PEM shapes, mixed case, no-over-redaction
of legitimate "primary key" messages, length truncation, control-character
neutralization, and update-payload non-leakage.

## Wave 5.2 — Sprint 5G.2 Intent Classification Runtime Fix

Status: Completed and merged.

Root cause: `app/router/classifier.py` treated `"fungsi"` ("function") as
an undifferentiated technical keyword. A purely informational Telegram
request ("Jelaskan dalam 3 kalimat apa fungsi Executive Control Tower di
NOVA.") was misclassified TECHNICAL, which routed `/ask` into the review
provider chain and failed with HTTP 429 — confirmed both via Telegram and
via direct classifier reproduction with no network involved.

Fix: technical vocabulary split into explicit ACTION verbs (always
TECHNICAL) and DOMAIN nouns (TECHNICAL by default, suppressed to GENERAL
only when the sentence itself is an explanatory/informational question,
e.g. "Jelaskan fungsi X" / "What is X"). The same guard applies to a single
weak Strategy keyword. Google Workspace, Presentation, and Academic
routing are unaffected — those categories' concrete phrase/domain matches
remain intent-agnostic by design. No provider, dispatch, Night Shift,
Control Tower, or Google Workspace code touched; classifier-only change.

See `docs/SPRINT_5G2.md` for the full classification design, precedence
decision, acceptance-case matrix, and test evidence.

## Wave 6 — NOVA Executive Office Foundation (architecture frozen)

Status: Architecture/contracts FROZEN by ChatGPT/Control Tower. Not yet
implemented.

Shared-contract design for five parallel Codex worktree sprints — 7A
(Executive Workflow), 7B (Knowledge Operations), 7C (Morning Executive
Brief), 7D (Agent Registry & Assignment), 7E (Dashboard Skeleton) — is
complete and frozen. See `docs/WAVE_6_SHARED_CONTRACTS.md` for the contract
evaluation, ten architecture decisions (AD-W6-01…10), ownership matrix, and
integration order, and `docs/SPRINT_7A.md` through `docs/SPRINT_7E.md` for
each mini-sprint's full spec.

Naming: this initiative was drafted under the working label "Wave 5," which
would have collided with this log's existing Sprint 5G-family Wave 5
entries above; it was renamed to Wave 6 as part of the freeze. Sprint 7E's
dashboard is frozen as a fully separate, explicitly-invoked process —
`app/main.py` is never touched by it and never starts it. The 7A/7D
boundary (`WorkItem`/workflow vs. `AgentAssignment`/assignment state) is
frozen with a narrow, named read interface between them
(`get_active_assignment_summary()`), documented in `docs/WAVE_6_SHARED_CONTRACTS.md`
AD-W6-01. No application code has been written — this entry records
architecture preparation only.

## Wave 6 — Integration review (7A + 7B + 7D combined)

Status: 7A (Executive Workflow), 7B (Knowledge Operations), and 7D (Agent
Registry & Assignment) individually reviewed and merged onto
`integration/wave6-core`; independent cross-sprint integration review
completed on the combined branch.

- 7A reviewed: `owner_for()`/`next_action_for()` verified against a real
  `AgentAssignmentService` (not only the stub used by 7A's own tests);
  unassigned/terminal-assignment fallback confirmed safe;
  `control_tower.agent_assignments=None` (7D absent) still derives owner
  correctly.
- 7B reviewed: `KnowledgeService` confirmed to write only its own additive
  tables, read `memory`/`control_tower` state only through existing public
  read methods, apply `SENSITIVE_CONTENT_PATTERN` before every write, and
  perform no network calls.
- 7D reviewed: `AgentAssignmentService` confirmed to route all execution
  exclusively through `DispatchService`/`ApprovalService` (no raw SQL
  against `dispatches`/`approvals`); `get_active_assignment_summary()`
  confirmed to exclude terminal assignments (`completed`/`cancelled`/
  `reassigned`) from the active-owner read path.
- Integration branch combined: `app/main.py` initialization order verified
  (memory → execution → night_shift → registry → approvals → dispatch →
  agent_assignments → control_tower(agent_assignments=...) → dissertation →
  knowledge → provider → Telegram application), no service dropped or
  double-initialized; `app/telegram_bot.py` verified to register every
  7A/7D/7B command exactly once, `HELP_MESSAGE` lists all of them, and the
  generic text fallback stays registered after every command handler.
- One integration defect fixed: `app/control_tower/service.py` was missing
  the `TYPE_CHECKING`-only import of `AgentAssignmentService` for its
  optional constructor parameter's forward-reference annotation (present
  for `ExecutionService`/`NightShiftService` but not added when the 7D
  injection point landed) — added, no behavior change.
- New cross-sprint integration tests added:
  `tests/test_wave6_integration.py` — real (non-stub)
  `AgentAssignmentService` + `ControlTowerService` wiring, `build_application()`
  carrying all three sprints' services and handlers, combined idempotent
  initialization against one temporary SQLite database, and a structural
  check that `app/knowledge/` never references `agent_assignments`/
  `control_tower_work_items`.
- Combined regression result: 734 passed (723 pre-review baseline + 11 new
  Wave 6 integration tests), no failures, no skips.
- Merge intentionally left uncommitted per the review's operating
  constraints — no commit, merge, or push performed by this review.

Next dependency stage: **7C (Morning Executive Brief)**, which reads 7A's
`owner_for()`/`next_action_for()` and 7D's `get_active_assignment_summary()`
once this branch is accepted, per `docs/WAVE_6_SHARED_CONTRACTS.md` §5's
integration order (7A/7B/7D → 7C → 7E).

## Sprint 7C — Morning Executive Brief

Status: deterministic, read-only composition implemented on the Sprint 7C
worktree; independently reviewed and corrected before integration. See
`docs/executive-morning-brief.md`.

Independent review found the first implementation folded the new executive
brief into the existing `/morning` command and, to source it, added three
new read methods to `app/control_tower/{repository,service}.py`
(`list_decisions()`, `list_work_items()`, `list_pending_approvals()`). Both
choices directly contradict the frozen `docs/SPRINT_7C.md`: §2/§14 require a
**new**, distinct `/execbrief` command specifically so `/morning`'s existing,
tested behavior is not changed, and §13 explicitly prohibits any edit to
`app/control_tower/**` (7A's exclusive territory per
`docs/WAVE_6_SHARED_CONTRACTS.md` §4). This was a contract violation, not an
acceptable deviation — corrected as follows:

- `app/control_tower/repository.py` and `app/control_tower/service.py` are
  reverted to their pre-7C state (byte-identical); zero edits remain.
- `/morning` is restored to its original, unmodified handler and output.
- A new `/execbrief` command is added, backed by the same
  `ExecutiveBriefService` (one composition layer, no duplicated business
  logic), registered exactly once, after every other Wave 6 command and
  before the generic text fallback.
- `ExecutiveBriefService` now sources "waiting for decision" and "recent
  decisions" using only pre-existing public reads
  (`ControlTowerService.get_today_priorities()`, `.list_approvals()`,
  `.owner_for()`, `.next_action_for()`, `.unresolved_blocker_count()`, and
  the already-public `.repository.list_work_items()` /
  `.repository.list_decisions_for_project()`) instead of the removed
  control-tower-side additions.

Full regression re-run after the fix; see the review record for the exact
pass count.
