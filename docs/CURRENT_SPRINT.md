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
