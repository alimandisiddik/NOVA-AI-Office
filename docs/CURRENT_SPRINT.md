# NOVA AI Office — Current Sprint

> **Always update this file at the start and end of every sprint.**
> Keep one entry per sprint. Archive completed sprints to `docs/SPRINT_<n>.md`
> when the next sprint begins.

---

## Sprint 5A — Always-On Runtime

| Field | Value |
|---|---|
| **Status** | 🚧 In Progress — 2026-08-06 |
| **Version target** | 5.0.0 |
| **Owner** | NOVA AI Office |

---

## Objective

Provide an always-on runtime for NOVA AI Office using macOS `launchd`, ensuring the bot runs automatically without requiring an active Terminal window, restarts on failure, and manages logs safely.

## Context — What Has Been Delivered

| Sprint | Key deliverables | Status |
|---|---|---|
| **1.1 - 3 corrective** | Repo foundation, SQLite Memory, Telegram, NL parser, Provider-agnostic router, Execution orchestration, Shared security pattern | ✅ Done |
| **4A** | Read-only Provider Gateway via httpx, NineRouter adapter, circuit breaker | ✅ Done |
| **4B** | Provider Fallback | ✅ Done |
| **5A** | Always-On Runtime via macOS `launchd` | 🚧 In Progress |

## Remaining Debt

| Item | Priority | Notes |
|---|---|---|
| Async worker queue for real adapter dispatch | Medium | Needed before any real adapter is activated |
| Full Telegram mock tests for execution handlers | Low | Covered by unit tests; integration tests deferred |
| Real adapter implementation | Blocked | Requires security review before activation |
| Per-execution artifact path enforcement | Low | `LocalDeterministicAdapter` writes no files; enforce on real adapters |

---

*Update this file when sprint status, scope, or acceptance criteria change.*
