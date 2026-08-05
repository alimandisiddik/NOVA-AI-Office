# Sprint 3A — Provider-Agnostic Model Router Interface

## Status

✅ **Complete** — 2026-08-06

---

## Objective

Build a provider-agnostic planning and routing layer for NOVA without calling
any real AI provider.  All classification, risk assessment, and plan generation
is deterministic and local-only.

---

## Delivered

### New package: `app/router/`

| Module | Responsibility |
|---|---|
| `app/router/__init__.py` | Package entry point; re-exports public API |
| `app/router/roles.py` | Logical role registry |
| `app/router/workflows.py` | Workflow registry |
| `app/router/classifier.py` | Deterministic intent → workflow classifier |
| `app/router/risk.py` | Risk classifier and approval policy |
| `app/router/planner.py` | Execution-plan generator + Telegram formatters |

### Logical Role Registry (`app/router/roles.py`)

Five roles registered as frozen dataclasses.  Provider status is always
`NOT_CONNECTED` until a real adapter is introduced.

| Role ID | Provider Label | Responsibility |
|---|---|---|
| `CONTROL_TOWER` | ChatGPT | Executive orchestration and handoffs |
| `WORKSPACE_KNOWLEDGE` | Gemini | Knowledge retrieval and synthesis |
| `TECHNICAL_ARCHITECT` | Claude | Architecture, code review, engineering specs |
| `EXECUTION_WORKER` | Codex | Deterministic implementation and file outputs |
| `FAST_ROUTER` | Configurable lightweight model | Low-latency triage and classification |

### Workflow Registry (`app/router/workflows.py`)

Seven workflows registered, each with primary and support roles.

| Workflow | Primary Roles |
|---|---|
| `GENERAL` | CONTROL_TOWER |
| `STRATEGY` | CONTROL_TOWER |
| `GOOGLE_WORKSPACE` | WORKSPACE_KNOWLEDGE |
| `TECHNICAL` | TECHNICAL_ARCHITECT, EXECUTION_WORKER |
| `PRESENTATION` | CONTROL_TOWER, WORKSPACE_KNOWLEDGE |
| `ACADEMIC` | WORKSPACE_KNOWLEDGE |
| `FAST` | FAST_ROUTER |

### Deterministic Intent Classifier (`app/router/classifier.py`)

Rule-based, keyword-driven classifier.  Priority order (first match wins):

1. Fast patterns (single-word trivial messages)
2. Technical keywords
3. Google Workspace keywords (multi-word phrases checked first)
4. Presentation keywords (checked before Academic to avoid false ACADEMIC hits)
5. Academic keywords
6. Strategy keywords
7. Default → `GENERAL`

Returns `ClassificationResult` with `workflow_id`, `confidence` (HIGH / MEDIUM / LOW),
and `matched_rule` for traceability.

### Risk Classifier + Approval Policy (`app/router/risk.py`)

Three risk levels and three approval modes:

| Risk | Approval | Trigger |
|---|---|---|
| HIGH | REQUIRED | Destructive-pattern regex match OR `GOOGLE_WORKSPACE` workflow |
| MEDIUM | NOTIFY | `STRATEGY`, `PRESENTATION`, or `ACADEMIC` workflow |
| LOW | NONE | `GENERAL`, `TECHNICAL`, or `FAST` workflow |

All HIGH-risk signals are matched with **word/token boundaries** (compiled
`re.Pattern` with `\b` anchors) so that words like `commitment`, `impostor`,
and harmless explanatory prose do not trigger false positives.

**Destructive shell and database commands:**
`rm -rf` (all flag orderings: `-rf`, `-fr`, `-Rf`, `-fR`),
`git reset --hard`, `git clean`, force-push (`--force`/`-f`),
`DROP TABLE`, `TRUNCATE TABLE`, `sudo`, `chmod 777`,
`shutdown`, `reboot`, `mkfs`, `dd if=`, `curl|sh`, `wget|sh`.

**High-risk action keywords (word-boundary anchored):**
`send`, `kirim`, `publish`, `post`, `delete`, `hapus`, `remove`,
`deploy`, `push`, `commit`, `overwrite`, `update production`,
`schedule`, `jadwalkan`, `email`, `broadcast`.

### Execution-Plan Generator (`app/router/planner.py`)

`generate_plan(message)` orchestrates classification → workflow lookup →
role resolution → risk assessment → plan assembly.  Returns an immutable
`ExecutionPlan` dataclass containing:
- original message
- `ClassificationResult`
- resolved `Workflow`
- resolved primary and support `Role` tuples
- `RiskAssessment`
- advisory notes (approval warnings, NOT_CONNECTED notice)

Three formatters:
- `format_plan(plan)` — full Telegram message for `/plan`
- `format_route(plan)` — compact summary for `/route`
- `format_router_status(roles, workflows)` — status overview for `/router_status`

### New Telegram Commands (`app/telegram_bot.py`)

| Command | Handler | Purpose |
|---|---|---|
| `/route <message>` | `route_command` | Compact routing summary |
| `/plan <message>` | `plan_command` | Full execution plan |
| `/router_status` | `router_status_command` | All roles and workflows |

All three commands respect the existing `_require_authorized_user` guard.

### Unit Tests

| File | Tests | Notes |
|---|---|---|
| `tests/test_router_roles.py` | 7 | |
| `tests/test_router_workflows.py` | 8 | |
| `tests/test_router_classifier.py` | 27 | |
| `tests/test_router_risk.py` | 52 | Extended by corrective pass — destructive-pattern coverage, false-positive guards, state invariants |
| `tests/test_router_planner.py` | 18 | |

**Total Sprint 3A tests: 112.  Total suite: 236 tests, all passing.**

*Note: `test_router_risk.py` grew from 17 (original Sprint 3A) to 52 during the
corrective pass that added boundary-based pattern coverage and false-positive tests.*

---

## Acceptance Criteria — Verification

| Criterion | Status |
|---|---|
| Roles and workflows are registered | ✅ |
| Requests can be classified deterministically | ✅ |
| Execution plans include roles, workflow, risk, and approval | ✅ |
| Provider status remains NOT_CONNECTED | ✅ |
| `/route`, `/plan`, `/router_status` work | ✅ |
| All prior commands remain operational | ✅ (34 prior tests still pass) |
| All tests pass | ✅ 236/236 (including corrective-pass additions) |
| Compile checks pass | ✅ |
| No secrets or database files are tracked | ✅ |

---

## Constraints Upheld

- No call to OpenAI, Gemini, Claude, Codex, or any external provider.
- No new API keys added.
- SQLite user data not modified.
- Sprint 1, Sprint 2, Sprint 2.1, Sprint 2.2 functionality fully preserved.
- Not committed or pushed — awaiting human review.

---

## Architectural Position

```text
Telegram Transport (app/telegram_bot.py)
    │
    ├── /route, /plan, /router_status
    │       │
    │       ▼
    │   app/router/
    │       ├── classifier.py   ← deterministic keyword rules
    │       ├── risk.py         ← risk level + approval mode
    │       ├── planner.py      ← assembles ExecutionPlan
    │       ├── roles.py        ← role registry (NOT_CONNECTED)
    │       └── workflows.py    ← workflow registry
    │
    └── /project, /task, /note, … (existing memory commands unchanged)
            │
            ▼
        app/memory/  (unchanged)
```

---

## Files Changed

| Path | Change |
|---|---|
| `app/router/__init__.py` | New — package entry point |
| `app/router/roles.py` | New — role registry |
| `app/router/workflows.py` | New — workflow registry |
| `app/router/classifier.py` | New — intent classifier |
| `app/router/risk.py` | New — risk + approval policy |
| `app/router/planner.py` | New — plan generator + formatters |
| `app/telegram_bot.py` | Modified — router imports, HELP_MESSAGE, 3 handlers, 3 registrations |
| `tests/test_router_roles.py` | New — 7 tests |
| `tests/test_router_workflows.py` | New — 8 tests |
| `tests/test_router_classifier.py` | New — 27 tests |
| `tests/test_router_risk.py` | New — 17 tests |
| `tests/test_router_planner.py` | New — 18 tests |
| `docs/SPRINT_3A.md` | New — this document |
