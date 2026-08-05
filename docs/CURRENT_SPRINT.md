# NOVA AI Office — Current Sprint

> **Always update this file at the start and end of every sprint.**
> Keep one entry per sprint. Archive completed sprints to `docs/SPRINT_<n>.md`
> when the next sprint begins.

---

## Sprint 4A — Secure Provider Gateway

| Field | Value |
|---|---|
| **Status** | ✅ Complete — 2026-08-06 (corrective pass applied) |
| **Version target** | 4.0.0 |
| **Owner** | NOVA AI Office |

---

## Objective

Introduce governed, controlled execution orchestration on top of the
Sprint 3A provider-agnostic router.  Every execution is classified, risk-assessed,
audit-logged, and state-machine validated before any adapter is called.

---

## Sprint Dependency

**Sprint 3 depends on the reviewed Sprint 3A risk engine.**

`app/router/` is the **single shared routing and risk-policy layer**.
`app/router/risk.assess_risk` is the authoritative source of risk classification
for both the router commands (`/route`, `/plan`) and the execution service
(`app/execution/service.py`).

> Do NOT create a duplicate policy classifier in `app/execution/` or anywhere
> else.  All risk assessment must flow through `app.router.risk.assess_risk`.

---

## Context — What Has Been Delivered

| Sprint | Key deliverables | Status |
|---|---|---|
| **1.1** | Repo foundation, `.gitignore`, `pyproject.toml`, folder boundaries, security policy | ✅ Done |
| **2.1** | SQLite Workspace Memory, 10 Telegram commands, 19 passing unit tests | ✅ Done |
| **2.2** | Deterministic NL parser (`app/natural_language.py`), clarification flow, full NL test coverage | ✅ Done |
| **3A** | Provider-agnostic model router (`app/router/`): classifier, risk engine, planner, roles, workflows; 77 new tests | ✅ Done |
| **3** | Execution orchestration (`app/execution/`): schema, repository, service, adapter, formatters; 85 new tests | ✅ Done |
| **3 corrective** | Strengthened risk patterns, shared `SENSITIVE_CONTENT_PATTERN`, service-layer limit enforcement, per-execution reconcile audit, auth-before-usage in route/plan handlers, Indonesian wording | ✅ Done |
| **4A** | Read-only Provider Gateway via `httpx`, NineRouter adapter, `/ask` & `/providerstatus`, audit schema, circuit breaker, retry bounds | ✅ Done |

---

## Sprint 3A — Status and What Was Delivered

Sprint 3A is **complete**. See `docs/SPRINT_3A.md` for the full record.

### Key Sprint 3A deliverables (required by Sprint 3)

- `app/router/risk.py` — risk classifier with token/regex-boundary destructive
  patterns for: `rm -rf`, `git reset --hard`, `git clean`, force push,
  `drop table`, `truncate table`, `sudo`, `chmod 777`, `shutdown`, `reboot`,
  `mkfs`, `dd if=`, `curl|sh`, `wget|sh`, `delete`, `commit`, `push`, and all
  original high-risk action keywords.
- Word/token boundaries prevent false positives for words like `commitment`,
  `impostor`, and harmless explanatory text.
- `app/router/classifier.py` — deterministic intent → workflow classifier.
- `app/router/planner.py` — plan generator + Telegram formatters.
- `app/router/roles.py` / `app/router/workflows.py` — registries.

---

## Sprint 3 — Status

Sprint 3 is **complete** with corrective pass applied on 2026-08-06.

### Canonical execution states (exactly six — no additions)

| State | Description |
|---|---|
| `created` | Initial row created |
| `awaiting_approval` | HIGH-risk; waiting for human confirmation |
| `queued` | Approved or LOW/MEDIUM risk; ready for dispatch |
| `running` | Adapter is executing |
| `completed` | Successful terminal state |
| `failed` | Failure terminal state (also used for cancellation) |

**Cancellation** transitions any non-terminal state to `failed` with
`event=cancelled` in the audit log.  There is no `cancelled` state.

### Limit enforcement

`max_execution_seconds` and `max_output_bytes` are enforced in
`ExecutionService._dispatch()` at the service layer, independently of
`LocalDeterministicAdapter` simulation helpers.

### Audit trail

One `failed` audit entry is written per reconciled execution in
`ExecutionService.initialize()`.  Repeated initialization does not duplicate
entries because `reconcile_running_to_failed_with_ids()` only updates rows
with `state='running'`, which no longer applies after the first call.

### Shared security pattern

`SENSITIVE_CONTENT_PATTERN` is defined once in `app/security.py` and imported
by both `app/memory/services.py` and `app/execution/service.py`.  No local
copies exist.

### Authorization

`/route` and `/plan` commands check `_require_authorized_user` **before**
processing arguments or returning usage strings, consistent with all other
protected commands.

---

## Accepted Limitations

1. `LocalDeterministicAdapter` does not invoke any real capability — it exists
   only to prove the orchestration lifecycle end-to-end.
2. `/run` dispatches LOW/MEDIUM risk executions synchronously in the Telegram
   handler.  For the deterministic in-process adapter this is negligible; real
   adapters would require an async worker queue.
3. Telegram handler tests are integration-style; full Telegram mock tests are
   not included in this sprint.
4. The wall-clock timeout check in `_dispatch` measures total elapsed time
   including Python overhead; for the deterministic adapter this is always well
   under the 30-second limit.

---

## Remaining Debt

| Item | Priority | Notes |
|---|---|---|
| Async worker queue for real adapter dispatch | Medium | Needed before any real adapter is activated |
| Full Telegram mock tests for execution handlers | Low | Covered by unit tests; integration tests deferred |
| Real adapter implementation | Blocked | Requires security review before activation |
| Per-execution artifact path enforcement | Low | `LocalDeterministicAdapter` writes no files; enforce on real adapters |

---

## Test Suite

| File | Tests | Category |
|---|---|---|
| `tests/test_router_risk.py` | extended | Includes all destructive patterns + false-positive guards |
| `tests/test_execution_service.py` | extended | Includes service-layer limit enforcement + reconcile audit |
| All other tests | passing | 236 total tests green |

---

## Module Boundaries

| Layer | Path |
|---|---|
| Entry point | `app/main.py` |
| Configuration | `app/config.py` |
| Security + shared patterns | `app/security.py` |
| Telegram transport | `app/telegram_bot.py` |
| NL intent parser | `app/natural_language.py` |
| **Router (shared policy layer)** | `app/router/` |
| Execution orchestration | `app/execution/` |
| Memory service | `app/memory/services.py` |
| Repositories | `app/memory/repositories.py` |
| Database | `app/memory/database.py` |

---

## Key Commands

```bash
source .venv/bin/activate

python -m pytest -ra
python -m pytest tests/test_router_risk.py tests/test_execution_service.py -v
python -m py_compile app/router/risk.py app/execution/service.py app/security.py
git diff --check
git check-ignore -v .env
```

---

## Links

| Document | Path |
|---|---|
| Sprint 3A archive | `docs/SPRINT_3A.md` |
| Sprint 3 archive | `docs/SPRINT_3.md` |
| Architecture | `docs/ARCHITECTURE.md` |
| Security policy | `docs/SECURITY.md` |
| Agent registry | `docs/AGENT_REGISTRY.md` |
| Roadmap | `docs/ROADMAP.md` |
| Sprint 1.1 archive | `docs/SPRINT_01.md` |

---

*Update this file when sprint status, scope, or acceptance criteria change.*
