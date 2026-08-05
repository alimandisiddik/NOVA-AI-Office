# Sprint 3 — Execution Orchestration

## Status

✅ **Complete** — 2026-08-06

---

## Objective

Build one usable local execution-orchestration capability for NOVA:

- accept execution requests through Telegram;
- classify risk using the Sprint 3A router;
- reject sensitive input before persistence;
- create immutable execution records;
- require explicit approval for destructive or irreversible requests;
- execute only through `LocalDeterministicAdapter`;
- expose execution status and cancellation;
- maintain a complete audit trail.

No real AI provider, shell, subprocess, or network call is made.

---

## Scope

| In scope | Out of scope |
|---|---|
| `app/execution/` package (6 modules) | CodexAdapter or any real provider adapter |
| `executions` + `execution_audit_log` tables | Altering existing tables |
| `/run`, `/runapprove`, `/runstatus`, `/cancelrun` Telegram commands | Any new Telegram commands beyond these four |
| `LocalDeterministicAdapter` | Real subprocess, shell, or provider execution |
| Sensitive-input guard (reuses `SENSITIVE_CONTENT_PATTERN`) | External API keys or credentials |
| Additive schema migration | Database migration tooling |
| 85 new tests | Refactoring unrelated modules |

---

## Architecture

```text
Telegram Transport (app/telegram_bot.py)
    │
    ├── /run       → ExecutionService.submit()
    ├── /runapprove → ExecutionService.approve()
    ├── /runstatus  → ExecutionService.get_status()
    └── /cancelrun  → ExecutionService.cancel()
                │
                ▼
        app/execution/
            service.py          ← state machine, approval, sensitive guard, audit
                │
                ├── repository.py    ← parameterized SQL only
                │       │
                │       └── MemoryDatabase (existing)
                │
                ├── adapter.py       ← LocalDeterministicAdapter (no shell, no provider)
                │
                ├── models.py        ← ExecutionRecord, AuditEntry, ExecutionState
                ├── schema.py        ← additive CREATE TABLE IF NOT EXISTS
                └── formatters.py   ← Telegram message rendering
```

---

## Exact State-Transition Matrix

| From | To | Trigger |
|---|---|---|
| `created` | `awaiting_approval` | HIGH-risk submission |
| `created` | `queued` | LOW/MEDIUM-risk submission |
| `awaiting_approval` | `queued` | Valid approval by authorized user |
| `awaiting_approval` | `failed` | Cancellation / rejection via `/cancelrun` |
| `queued` | `running` | Adapter dispatch (atomic CAS) |
| `queued` | `failed` | Cancellation or dispatch failure |
| `running` | `completed` | Adapter returns success |
| `running` | `failed` | Error, timeout, output limit, or cancellation |
| `completed` | — | **Terminal — no transitions permitted** |
| `failed` | — | **Terminal — no transitions permitted** |

All unspecified transitions are rejected by `transition_state()` before any SQL write.

---

## Telegram Commands

| Command | Handler | Description |
|---|---|---|
| `/run <instruction>` | `run_command` | Create and dispatch (or queue for approval) an execution |
| `/runapprove <id>` | `runapprove_command` | Approve an `awaiting_approval` execution |
| `/runstatus <id>` | `runstatus_command` | Return current execution state and metadata |
| `/cancelrun <id>` | `cancelrun_command` | Cancel any non-terminal execution (also serves as rejection path) |

- All ID-based commands accept a bare integer only.
- All commands enforce `_require_authorized_user` before any operation.

---

## LocalDeterministicAdapter

**Constraints** (in-process only):

| Property | Value |
|---|---|
| Shell | `False` — never used |
| Subprocess | None — zero subprocess calls |
| Network | None — zero socket or HTTP calls |
| Provider SDK | None — no OpenAI, Gemini, Claude, Codex |
| Repository execution | Not permitted |
| User-path injection | Not possible — only integer execution IDs are used |

**Behavior**: derives a deterministic simulated output size from the first 8 hex digits of the SHA-256 instruction hash. Returns `AdapterResult(success=True, ...)` for valid hashes within the output limit; `success=False` otherwise.

**Future adapter constraints** (documented but not implemented):

- `shell=False`; argv-only invocation; no user-controlled shell string.
- Strict environment allowlist: `PATH` (restricted), `LANG`, `LC_ALL`.
- Exclude: `SSH_AUTH_SOCK`, `SSH_AGENT_PID`, all provider keys/tokens/credentials, all host secrets.
- No inherited host environment by default.
- No execution inside the NOVA repository.
- No path derived from user-supplied text.

---

## Approval Policy

- Restricted to the configured `TELEGRAM_ALLOWED_USER_ID`.
- Tied to one immutable execution ID.
- Non-transferable; non-reusable across executions.
- Valid only when execution is in `awaiting_approval`.
- Persisted with `approved_by` (Telegram user ID) and `approved_at` (UTC ISO-8601).
- Duplicate approval raises `ApprovalError` — never dispatches twice.
- Unauthorized approval raises `ApprovalError` — never changes state.
- Enforcement is in the **service layer**, not only in Telegram handlers.

---

## Sensitive Input Handling

- Reuses `SENSITIVE_CONTENT_PATTERN` from `app/memory/services.py`.
- Detection happens **before** any persistence — the raw instruction is never stored.
- The SHA-256 hex digest of the raw instruction is stored in `executions.instruction_hash`.
- The raw instruction is never:
  - stored in `executions` or `execution_audit_log`;
  - printed, logged, or echoed to Telegram;
  - included in any exception message;
  - passed to the adapter.
- A generic rejection message is returned with no sensitive content.

Detected patterns:
`telegram_bot_token`, `api_key`, `password`, `credential`, `secret`, `Authorization: Bearer`, `BEGIN * PRIVATE KEY`, and `KEY=VALUE` environment-variable patterns.

---

## Database Schema (additive only)

```sql
-- New tables only — existing tables untouched
CREATE TABLE IF NOT EXISTS executions (
    id               INTEGER PRIMARY KEY,
    instruction_hash TEXT    NOT NULL,           -- SHA-256 hex, never raw instruction
    risk_level       TEXT    NOT NULL CHECK (risk_level IN ('LOW','MEDIUM','HIGH')),
    workflow_id      TEXT    NOT NULL,
    state            TEXT    NOT NULL DEFAULT 'created'
                     CHECK (state IN ('created','awaiting_approval','queued',
                                      'running','completed','failed')),
    approved_by      INTEGER,                    -- Telegram user_id, NULL until approved
    approved_at      TEXT,                       -- UTC ISO-8601, NULL until approved
    result_summary   TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_audit_log (
    id           INTEGER PRIMARY KEY,
    execution_id INTEGER NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    event        TEXT    NOT NULL,
    actor        TEXT    NOT NULL,               -- "system" | "user:<id>"
    detail       TEXT    NOT NULL DEFAULT '',    -- safe, non-sensitive
    created_at   TEXT    NOT NULL
);
```

---

## Audit Behavior

Every meaningful lifecycle event creates an audit entry:

| Event | Actor | When |
|---|---|---|
| `created` | `user:<id>` | Execution record created |
| `approved` | `user:<id>` | Approval recorded |
| `running` | `system` | Adapter dispatch started |
| `completed` | `system` | Adapter returned success |
| `failed` | `system` | Adapter returned failure |
| `cancelled` | `user:<id>` | User cancelled via `/cancelrun` |

Audit entries never contain sensitive content. The `detail` field contains only safe operational context (workflow, risk level, hash prefix, state transitions).

---

## Duplicate-Dispatch Prevention

`transition_state()` uses an atomic compare-and-swap (CAS) SQL `UPDATE ... WHERE state = ?`. If the expected state does not match (because another dispatch already advanced it), `InvalidTransitionError` is raised and the second dispatch is blocked. The audit log records only one `approved` event.

---

## Restart Reconciliation

On `ExecutionService.initialize()`, `reconcile_running_to_failed()` updates all `running` rows to `failed` with a standard summary. This prevents permanently stuck executions after an unclean shutdown. `awaiting_approval` and `queued` executions are not affected.

---

## Timeout and Output Limits

- `LocalDeterministicAdapter.simulate_timeout()` — returns `AdapterResult(success=False, summary="Execution timed out...")`
- `LocalDeterministicAdapter.simulate_output_limit(bytes, limit)` — returns failure with byte counts
- Logical constants: `OUTPUT_BYTE_LIMIT = 65536` bytes, `EXECUTION_TIMEOUT_SECONDS = 30`
- The service transitions `running → failed` when the adapter returns `success=False`

---

## Cancellation Semantics

| State | `/cancelrun` behavior |
|---|---|
| `awaiting_approval` | Rejection path — transitions to `failed` |
| `queued` | Pre-dispatch cancellation — transitions to `failed` |
| `running` | Cooperative cancellation — transitions to `failed` |
| `completed` | **Rejected** — `InvalidTransitionError` |
| `failed` | **Rejected** — `InvalidTransitionError` |

---

## Path Safety

Execution-artifact paths are constructed only from trusted integer execution IDs. User-supplied instruction text, project names, and usernames are never used as path components. Resolved paths must remain within the configured execution root (enforced by policy; LocalDeterministicAdapter does not write any files).

---

## Tests

| File | Tests | Category |
|---|---|---|
| `tests/test_execution_schema.py` | 5 | Migration |
| `tests/test_execution_security.py` | 32 | Security |
| `tests/test_execution_service.py` | 43 | Functional + Reliability + corrective-pass additions |
| `tests/test_execution_formatters.py` | 10 | Output formatting |
| **Total Sprint 3 (execution)** | **90** | |
| **Total suite** | **236** | All passing |

*`test_execution_service.py` grew from 38 to 43 during the corrective pass that
added service-layer timeout enforcement, output-limit enforcement, per-execution
reconcile audit, idempotent-initialize, and shared-pattern identity tests.*

---

## Known Limitations

1. `LocalDeterministicAdapter` does not invoke any real capability — it exists only to prove the orchestration lifecycle.
2. ~~Individual execution-level audit entries for reconciled executions were not originally written.~~ **Resolved in corrective pass:** `reconcile_running_to_failed_with_ids()` returns the IDs of affected executions; `ExecutionService.initialize()` now writes one `event="failed"` audit entry per reconciled execution. Repeated initialization is idempotent.
3. `/run` dispatches LOW/MEDIUM risk executions synchronously in the Telegram handler — blocking the bot during adapter execution. For the deterministic in-process adapter this is negligible; real adapters would require an async worker queue.
4. Telegram handler tests are integration-style; full Telegram mock tests are not included in this sprint.

---

## Rollback Guidance

To roll back Sprint 3:

1. Stop NOVA (`Ctrl+C` on `scripts/run_local.sh`).
2. Remove the `app/execution/` directory.
3. Restore `app/telegram_bot.py` and `app/main.py` to their pre-Sprint-3 state via `git checkout`.
4. The `executions` and `execution_audit_log` tables will remain in the SQLite file but will be inert — existing memory tables and data are unaffected.
5. To remove the new tables: `DROP TABLE execution_audit_log; DROP TABLE executions;` (while NOVA is stopped).

---

## Security Acceptance Criteria

| Criterion | Status |
|---|---|
| Sensitive input rejected before persistence | ✅ PASS |
| Raw value absent from `executions` table | ✅ PASS |
| Raw value absent from `execution_audit_log` | ✅ PASS |
| Raw value not echoed to Telegram | ✅ PASS |
| Raw value not in exception messages | ✅ PASS |
| Unauthorized approval rejected | ✅ PASS |
| Approval bound to one execution ID | ✅ PASS |
| No cross-execution approval reuse | ✅ PASS |
| No subprocess call | ✅ PASS |
| No os.system call | ✅ PASS |
| Path not derived from instruction text | ✅ PASS |

## Functional Acceptance Criteria

| Criterion | Status |
|---|---|
| `/run` creates LOW-risk execution and queues it | ✅ PASS |
| `/run` creates HIGH-risk execution in `awaiting_approval` | ✅ PASS |
| `/runapprove <id>` approves only valid awaiting execution | ✅ PASS |
| `/runstatus <id>` returns current status | ✅ PASS |
| `/cancelrun <id>` rejects awaiting approval | ✅ PASS |
| `/cancelrun <id>` cancels queued execution | ✅ PASS |
| Deterministic adapter completes successfully | ✅ PASS |
| Invalid execution ID handled safely | ✅ PASS |

## Reliability Acceptance Criteria

| Criterion | Status |
|---|---|
| Duplicate approval does not dispatch twice | ✅ PASS |
| Atomic queued → running transition | ✅ PASS |
| Repeated dispatch attempt blocked | ✅ PASS |
| Running reconciled to failed on restart | ✅ PASS |
| Awaiting approval survives restart | ✅ PASS |
| Timeout produces failed | ✅ PASS |
| Output limit produces failed | ✅ PASS |
| Cancellation semantics for each non-terminal state | ✅ PASS |
| Terminal state cannot be changed | ✅ PASS |
| Invalid transitions rejected | ✅ PASS |

## Migration Acceptance Criteria

| Criterion | Status |
|---|---|
| Existing database initializes successfully | ✅ PASS |
| New tables created additively | ✅ PASS |
| Existing tables and data remain intact | ✅ PASS |
| Initialization is idempotent | ✅ PASS |

## Regression Acceptance Criteria

| Criterion | Status |
|---|---|
| All 111 prior tests still pass | ✅ PASS |
| Existing Telegram handlers remain registered | ✅ PASS |
| Existing commands retain their behavior | ✅ PASS |
| Sprint 3A router tests continue to pass | ✅ PASS |
