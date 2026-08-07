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

Status: Implementation under review / not yet merged

Key outcomes:

- singleton dissertation workspace with chapter focus tracking;
- source, evidence, research-note, gap, audit, and linkage persistence;
- sourced and bounded academic evidence with additive SQLite migrations;
- Control Tower references for academic work items and decisions;
- read-only `/dissertation` Telegram workspace views.

## Wave 5 — Sprint 5G Multi-Provider Fallback Hardening

Status: Implementation under review / not yet merged

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

## Wave 5.2 — Sprint 5G.2 Intent Classification Runtime Fix

Status: Implementation under review / not yet merged.

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
