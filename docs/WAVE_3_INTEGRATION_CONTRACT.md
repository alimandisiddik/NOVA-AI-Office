# Wave 3 Integration Contract — Agent Dispatch, Approval Operations & Full Night Shift Automation

## Status: Proposed (architecture, not yet implemented)

## Baseline

- `main` = `origin/main`, `HEAD` = `5b60e75`.
- Wave 2 complete: full regression = 392 passed.
- Merged: Sprint 5B (Executive Control Tower MVP, `app/control_tower/`),
  Sprint 5D (Google Calendar read-only, `app/google_workspace/calendar/`),
  Sprint 5E (Google Drive read-only, `app/google_workspace/drive/`),
  Sprint 5A.1 (Night Shift Runtime Foundation, `app/nightshift/`).
- This contract governs two sprints implemented **in parallel** on separate
  branches: **Sprint 5B.1 — Agent Dispatch & Approval Operations** and
  **Sprint 5F — Full Night Shift Automation**.

## Why this contract exists

Grounding research on the current tree surfaced three facts that make an
explicit contract necessary rather than optional:

1. **No canonical dispatch or approval system exists today.** Three
   independent, incompatible approval shapes already exist —
   `ExecutionService.approve()` (single-authorized-user check on
   `executions.state`), `NightShiftService.transition_night_job()`
   (state-machine only, no dedicated approve/reject method despite being
   named in the original `SPRINT_5A1.md` proposal), and
   `ControlTowerService.request_approval()`/`resolve_approval()`
   (`control_tower_approval_links`, which only aggregates the other two for
   read-only display). Sprint 5B.1 must pick one shape and generalize it —
   not add a fourth.
2. **Night Shift has no executor.** `NightShiftService.tick()` only flips
   `runtime_mode_state` and triggers `generate_morning_brief()`.
   `register_job_executor()` validates and discards; nothing invokes it.
   `app/main.py` does not even call `tick()` on a schedule yet. Sprint 5F is
   building the first real executor NOVA has ever had, and the one hard rule
   is that it must not become a second dispatch system.
3. **`app/main.py` and `app/telegram_bot.py` are the only files every prior
   sprint has touched additively**, always as one small block appended after
   the existing service chain. Both Wave 3 sprints need to touch both files.
   This contract defines the exact insertion order and a merge sequence that
   makes that safe.

---

## 1. Ownership boundaries

| Concern | Owner | Notes |
|---|---|---|
| `DispatchService` | **5B.1** | New module, `app/dispatch/service.py`. |
| `ApprovalService` | **5B.1** | New module, `app/dispatch/approvals.py`. Canonical approval authority for Wave 3 and later. |
| `AgentRegistry` | **5B.1** | Static allowlist, `app/dispatch/registry.py`, mirrors `app/providers/registry.py`. |
| Night Shift worker (`NightShiftWorker`) | **5F** | New module, `app/nightshift/worker.py`. Consumes 5B.1's interfaces; owns no dispatch/approval state. |
| Scheduler (tick registration, `job_queue.run_repeating`) | **5F** | Extends the existing `NightShiftService.tick()` (5A.1) by actually wiring it into `app/main.py`/`build_application`. 5B.1 does not touch the scheduler. |
| Retry **policy** (when/how often a night job retries, backoff schedule) | **5F** | Decided in `NightShiftWorker`, using `NightQueueJob.attempt_count` (new column, §9). |
| Retry **mechanism** (creating a new dispatch attempt, enforcing `max_attempts` at the dispatch level) | **5B.1** | `DispatchService.retry_dispatch()`. 5F calls it; never re-implements it. |
| Cancellation **mechanism** | **5B.1** | `DispatchService.cancel_dispatch()` is the only code path that writes a `cancelled` dispatch status. |
| Cancellation **trigger** (Telegram `/nightqueue cancel`, stale-lease reaping) | **5F** | Calls `DispatchService.cancel_dispatch()`; never sets `dispatches.status` directly. |
| Status synchronization | **5B.1** | `DispatchService.synchronize_status()` is the only writer of dispatch status transitions outside `dispatch()`/`retry_dispatch()`/`cancel_dispatch()`. 5F reads dispatch status only through `get_dispatch()`/`synchronize_status()`, never via raw SQL against `dispatches`. |
| Telegram commands on dispatch/approval entities (`/dispatch`, `/dispatches`, `/dispatchstatus`, `/approve`, `/reject`, `/canceldispatch`, `/retrydispatch`) | **5B.1** | New commands — none of these currently exist. `/approvals` (read-only, Sprint 5B) is extended, not replaced (§12). |
| Telegram commands on Night Shift queue entities (`/nightshift`, `/nightstatus`, `/nightqueue`, `/wake`) | **5F** | These were reserved-but-unimplemented in 5A.1/5B; 5F is the sprint that finally wires them, using the worker/dispatch interfaces, not raw `night_queue_jobs` mutation. |
| `dispatch_audit_log`, `approval_audit` | **5B.1** | Owns both tables and is the only writer. |
| `night_shift_audit_log` | **5F** (existing table, 5A.1-owned, 5F is the first sprint to write queue-level automation events into it — claim, lease, backoff, quiet-hours routing) | 5B.1 never writes to this table. |
| `app/main.py` edits | **Shared, ordered** | See §12 for exact insertion points and order. |
| `app/telegram_bot.py` edits | **Shared, ordered** | See §12. 5B.1's edit to `build_application()`'s signature must land first; 5F rebases onto it. |
| `docs/CURRENT_SPRINT.md` | **Shared, sequential** | 5B.1 adds its Wave 3 entry when it merges; 5F appends its own entry in a small follow-up diff after rebasing onto merged 5B.1. Neither sprint edits the other's line. |
| **Integration owner for Wave 3** | **Sprint 5B.1** | 5B.1 owns the interfaces 5F depends on, merges first, and is the tie-breaker for any ambiguity discovered during 5F's implementation. If 5F needs an interface change, it requests it from 5B.1 rather than forking the interface. |

---

## 2. Stable service interfaces

All methods are synchronous (SQLite, single-writer, matching every existing
service in the codebase). All raise typed exceptions from `app/dispatch/errors.py`
(§10) — never bare `Exception`/`ValueError` for anything caller-actionable.
Every state-changing method takes an `actor: str` (`"user:<telegram_id>"` or
`"system:<subsystem>"`, matching the repo-wide convention) and every method
that changes stored state does so inside one transaction that also writes its
audit row, mirroring `ControlTowerRepository.create_work_item`'s
`BEGIN IMMEDIATE … COMMIT` pattern.

### DispatchService (`app/dispatch/service.py`)

```python
class DispatchService:
    def __init__(self, database: MemoryDatabase, *, registry: AgentRegistry,
                 approvals: ApprovalService) -> None: ...

    def initialize(self) -> None: ...
        # apply_schema(); no reconciliation of "dispatching"/"running" rows to
        # failed on restart is performed here — see §4 "stale-update protection"
        # for why that is 5F's job (lease-based), not a blanket CAS like
        # ExecutionService.initialize()'s running->failed reconciliation.

    def create_dispatch(self, request: DispatchRequest, actor: str) -> DispatchRecord: ...
        # Validates agent_id/capability against AgentRegistry, validates
        # source_type, enforces idempotency (see §5). Sets initial status to
        # 'awaiting_approval' if ApprovalService/AgentRegistry classify the
        # capability as approval-required (§6), else 'pending'.

    def dispatch(self, dispatch_id: str, actor: str = "system:dispatch") -> DispatchRecord: ...
        # Valid only from 'pending' or 'approved'. CAS pending/approved -> dispatching
        # -> running -> {succeeded, failed, timed_out}. Raises ApprovalRequiredError
        # if called while status == 'awaiting_approval'.

    def get_dispatch(self, dispatch_id: str) -> DispatchRecord: ...
        # Raises DispatchNotFoundError.

    def list_dispatches(self, *, status: str | None = None, source_type: str | None = None,
                         agent_id: str | None = None, limit: int = 50) -> list[DispatchRecord]: ...

    def cancel_dispatch(self, dispatch_id: str, actor: str, reason: str) -> CancellationResult: ...
        # Valid from any non-terminal status. Terminal states raise
        # InvalidTransitionError. Always ends in 'cancelled'; no other target.

    def retry_dispatch(self, dispatch_id: str, actor: str) -> DispatchRecord: ...
        # Valid only from 'failed' or 'timed_out'. Raises RetryExhaustedError
        # if attempt_count >= max_attempts. Creates a new dispatch_attempts row,
        # CAS-transitions dispatches.status back to 'dispatching'.

    def synchronize_status(self, dispatch_id: str, *, expected_status: str,
                            observed_status: str, actor: str = "system:sync",
                            correlation_id: str | None = None) -> StatusSyncResult: ...
        # CAS: only writes if dispatches.status == expected_status at write time,
        # else returns StatusSyncResult(applied=False, ...) — never raises for a
        # stale caller, since sync is expected to race benignly against dispatch().
```

### ApprovalService (`app/dispatch/approvals.py`)

```python
class ApprovalService:
    def __init__(self, database: MemoryDatabase, *, authorized_user_id: int) -> None: ...
        # Single-authorized-user model, identical to ExecutionService's
        # self._authorized_user_id check — Wave 3 introduces no multi-approver
        # concept.

    def initialize(self) -> None: ...

    def request_approval(self, dispatch_id: str, requested_action: str, actor: str,
                          *, expires_at: str | None = None,
                          correlation_id: str | None = None) -> ApprovalRequest: ...
        # One open ('requested') approval per dispatch_id at a time — a second
        # call while one is already 'requested' returns the existing row
        # (idempotent), it does not create a duplicate.

    def approve(self, approval_id: str, approving_user_id: int) -> ApprovalDecision: ...
        # Raises ApprovalAuthorizationError if approving_user_id is not the
        # configured authorized user (mirrors ExecutionService.approve exactly).
        # Does NOT call DispatchService.dispatch() itself — returns the decision;
        # the caller (Telegram handler or NightShiftWorker) is responsible for
        # calling dispatch() next. This keeps ApprovalService ignorant of
        # DispatchService, avoiding a circular import (DispatchService already
        # depends on ApprovalService, not the reverse).

    def reject(self, approval_id: str, approving_user_id: int, reason: str) -> ApprovalDecision: ...

    def get_approval(self, approval_id: str) -> ApprovalRequest: ...
        # Raises ApprovalNotFoundError.

    def list_pending(self, *, source_type: str | None = None) -> list[ApprovalRequest]: ...

    def expire_or_close(self, approval_id: str, actor: str, reason: str) -> ApprovalDecision: ...
        # Justification (explicitly required by this contract's brief): Night
        # Shift jobs deferred to morning create an approval that, if nobody acts
        # on it for days, would otherwise sit in list_pending() forever and
        # pollute /approvals and the morning brief. This method moves a
        # 'requested' approval to 'expired' (not 'approved' — never automatic
        # approval) after its expires_at passes, or to 'cancelled' if the
        # underlying dispatch was cancelled out from under it. Only 5F's worker
        # (via recover_stale_job) and a future scheduled sweep call this with
        # actor='system:*'; no Telegram command exposes it as a user action.
```

### AgentRegistry (`app/dispatch/registry.py`)

Read-only static registry, same shape as `app/providers/registry.py`'s
`RegisteredModel`/`MODEL_REGISTRY`/`get_registered_model`. **No runtime
registration API is exposed** — the allowlist is a hardcoded tuple compiled
into the module, exactly like `MODEL_REGISTRY` and
`app/nightshift/classifier.py`'s `REGISTERED_JOBS`.

```python
@dataclass(frozen=True)
class RegisteredAgent:
    agent_id: str
    display_name: str
    category: str                    # matches app.control_tower.models.APPROVED_CATEGORIES
    capabilities: frozenset[str]      # e.g. {"draft_only", "read_only"}
    adapter_id: str                   # label resolved to a concrete adapter, §7
    enabled: bool

_AGENTS: tuple[RegisteredAgent, ...] = ( ... )  # §7
AGENT_REGISTRY: dict[str, RegisteredAgent] = {a.agent_id: a for a in _AGENTS}

def resolve_agent(agent_id: str) -> RegisteredAgent | None: ...
def validate_capability(agent_id: str, capability: str) -> None: ...
    # Raises UnknownAgentError or UnsupportedCapabilityError; returns None on success.
def list_agents(*, enabled_only: bool = True) -> list[RegisteredAgent]: ...
```

### Night Shift consumer interface (`app/nightshift/worker.py`, owned by 5F)

`NightShiftWorker` is the **only** code in 5F allowed to call
`DispatchService`/`ApprovalService`. It never writes to `dispatches`,
`dispatch_attempts`, or `approvals` directly.

```python
class NightShiftWorker:
    def __init__(self, database: MemoryDatabase, *, night_shift: NightShiftService,
                 dispatch: DispatchService, approvals: ApprovalService,
                 worker_id: str, lease_seconds: int = 600,
                 max_attempts: int = 3) -> None: ...

    def list_eligible_jobs(self, now: datetime, *, limit: int = 20) -> list[NightQueueJob]: ...
        # status='queued', runtime mode == 'night_shift' (or is_manual_override
        # active-> still respects quiet/maintenance per NightShiftService rules),
        # eligible_after IS NULL or <= now, dispatch_id IS NULL.

    def claim_job(self, job_id: str, now: datetime) -> NightQueueJob: ...
        # CAS UPDATE ... WHERE status='queued' AND lease_worker_id IS NULL.
        # Raises NightJobAlreadyClaimedError if the CAS misses (0 rows affected).

    def execute_via_dispatch(self, job: NightQueueJob) -> NightShiftDispatchEnvelope: ...
        # Builds a DispatchRequest (source_type='night_shift_job', source_id=job.job_id,
        # idempotency_key=f"night_shift_job:{job.job_id}:attempt"), calls
        # dispatch.create_dispatch() then, only if the resulting status is
        # 'pending' (approval-free), calls dispatch.dispatch(). If the resulting
        # status is 'awaiting_approval', does NOT call dispatch() — returns the
        # envelope for the caller to route through defer_for_approval().

    def record_result(self, job_id: str, envelope: NightShiftDispatchEnvelope) -> NightQueueJob: ...
        # Maps DispatchRecord.status -> NightQueueJob.status per §4's mapping
        # table, calls NightShiftService.transition_night_job(), releases the
        # lease (lease_worker_id, lease_expires_at -> NULL).

    def defer_for_approval(self, job: NightQueueJob, dispatch_id: str) -> NightQueueJob: ...
        # Calls approvals.request_approval(dispatch_id, ..., expires_at=<next
        # morning_brief_time + 24h>), transitions the job to 'awaiting_approval'
        # via NightShiftService.transition_night_job(), releases the lease
        # (an awaiting-approval job is not "in progress" and must not block a
        # concurrency slot — see §8).

    def recover_stale_job(self, job_id: str, now: datetime) -> NightQueueJob: ...
        # Only valid when lease_expires_at < now. Calls
        # dispatch.cancel_dispatch(reason="stale lease") if a dispatch_id is
        # attached and its status is 'dispatching'/'running', then releases the
        # lease and either re-queues (attempt_count < max_attempts) or
        # transitions to 'failed_safely' (attempt_count exhausted).
```

Transaction boundaries: `claim_job` and `recover_stale_job` are each a single
DB transaction (lease CAS). `execute_via_dispatch` spans **two** separate
transactions it does not control directly — one inside
`DispatchService.create_dispatch()`, one inside `DispatchService.dispatch()`
— by design: dispatch outcomes must be durable and auditable independent of
whether the Night Shift worker process itself survives the call. If the
worker crashes between the two calls, the dispatch is left in `pending`
un-dispatched; the next `tick()`'s stale-lease sweep (`recover_stale_job`)
picks it up because the job's lease will have expired without a
`record_result()`.

---

## 3. DTOs and identifiers

All DTOs are `@dataclass(frozen=True)` in `app/dispatch/models.py`, matching
`WorkItem`/`ExecutionRecord`/`NightQueueJob`'s existing style (plain
dataclasses, no ORM, no Pydantic — none is used anywhere else in the repo).

```python
@dataclass(frozen=True)
class DispatchRequest:
    source_type: str          # 'control_tower_work_item' | 'night_shift_job' | 'telegram_direct'
    source_id: str             # e.g. control_tower_work_items.item_id, or night_queue_jobs.job_id
    agent_id: str
    capability: str
    payload_ref: str           # a hash or short label — NEVER raw content (mirrors
                                # ExecutionService storing instruction_hash, not the instruction)
    idempotency_key: str
    requested_by: str          # 'user:<telegram_id>' | 'system:<subsystem>'
    correlation_id: str | None = None
    max_attempts: int = 3

@dataclass(frozen=True)
class DispatchRecord:
    dispatch_id: str           # str(uuid.uuid4()) — TEXT PK, cross-referenced by Telegram args
    source_type: str
    source_id: str
    agent_id: str
    capability: str
    status: str                 # §4 dispatch state machine
    attempt_count: int
    max_attempts: int
    idempotency_key: str
    correlation_id: str | None
    requested_by: str
    result_summary: str
    created_at: str
    updated_at: str

@dataclass(frozen=True)
class DispatchResult:
    dispatch_id: str
    attempt_number: int
    success: bool
    summary: str                # sanitized; never a raw provider/agent response (§11)
    ended_at: str

@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str            # str(uuid.uuid4()) — TEXT PK
    dispatch_id: str
    requested_action: str
    status: str                 # §4 approval state machine
    requested_by: str
    requested_at: str
    expires_at: str | None
    resolved_by: str | None = None
    resolved_at: str | None = None

@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    dispatch_id: str
    status: str                 # 'approved' | 'rejected' | 'cancelled' | 'expired'
    resolved_by: str
    resolved_at: str
    reason: str = ""

@dataclass(frozen=True)
class AgentCapability:
    agent_id: str
    capability: str
    approval_required: bool     # derived from §6 policy, not stored — computed at
                                 # AgentRegistry/DispatchService boundary each call

@dataclass(frozen=True)
class NightShiftDispatchEnvelope:
    job_id: str
    dispatch_id: str
    dispatch_status: str
    requires_approval: bool
    approval_id: str | None = None

@dataclass(frozen=True)
class StatusSyncResult:
    dispatch_id: str
    applied: bool                # False if the CAS missed (stale caller)
    previous_status: str
    new_status: str | None       # None if not applied

@dataclass(frozen=True)
class RetryDecision:
    dispatch_id: str
    will_retry: bool
    attempt_number: int | None   # the attempt about to be created, if will_retry
    reason: str                  # e.g. "attempts exhausted", "retrying"

@dataclass(frozen=True)
class CancellationResult:
    dispatch_id: str
    previous_status: str
    cancelled_at: str
    reason: str
```

**Identifiers**

| ID | Type | Generated by | Cross-referenced by |
|---|---|---|---|
| `dispatch_id` | `TEXT` (`uuid4`) | `DispatchService.create_dispatch` | Telegram `/dispatchstatus <id>`, `night_queue_jobs.dispatch_id` |
| `approval_id` | `TEXT` (`uuid4`) | `ApprovalService.request_approval` | Telegram `/approve <id>` / `/reject <id>` |
| `dispatch_attempts.id` | `INTEGER` autoincrement | SQLite rowid | Internal only — never exposed to Telegram args |
| `idempotency_key` | `TEXT`, caller-supplied | Caller (§5 format) | Enforced via `UNIQUE(source_type, source_id, idempotency_key)` |
| `correlation_id` | `TEXT`, optional | Originating Control Tower work item or caller | Threaded through `dispatch_audit_log`, `approval_audit`, and (if present) `control_tower_audit_log.correlation_id` |
| `source_id` | `TEXT` | Caller | `control_tower_work_items.item_id` or `night_queue_jobs.job_id` — **not** a foreign key (dispatch must not hard-depend on either table's schema; see §9) |
| `actor`/`requested_by` | `TEXT` | Caller | `"user:<telegram_id>"` \| `"system:<subsystem>"`, repo-wide convention |
| Timestamps | `TEXT`, ISO-8601 UTC | `utc_now()` (duplicated per-domain helper, same as every other domain) | — |
| Safe metadata | `TEXT` (JSON object) | Caller | Same allowlist discipline as `NightShiftService._metadata()`: object only, no `prompt`/`content`/`document`/`response`/`exception` keys |

**Design note on execution vs. dispatch:** `dispatch_id` is a wholly separate
identifier space from `executions.id` (Sprint 3's `ExecutionService`).
Wave 3 does **not** read or write the `executions` table. Where "connect
Control Tower work items to execution records" is required (§1, §12),
"execution record" means **dispatch record** — the objective is satisfied by
`DispatchRequest.source_id` referencing `control_tower_work_items.item_id`,
not by touching `app/execution/`.

---

## 4. State machines

### Dispatch states

`pending`, `awaiting_approval`, `approved`, `dispatching`, `running`,
`succeeded`, `failed`, `cancelled`, `rejected`, `timed_out`.

Terminal states: `succeeded`, `cancelled`, `rejected`. `failed` and
`timed_out` are **retry-eligible terminal states** — no ordinary transition
leaves them, but `retry_dispatch()` is explicitly allowed to (bounded by
`max_attempts`); once `max_attempts` is reached they become truly terminal.

Transition matrix (row = from, cell = allowed targets):

| From \ To | awaiting_approval | approved | dispatching | running | succeeded | failed | timed_out | cancelled | rejected |
|---|---|---|---|---|---|---|---|---|---|
| `pending` | ✅ (approval-required) | – | ✅ (approval-free) | – | – | – | – | ✅ | – |
| `awaiting_approval` | – | ✅ | – | – | – | – | – | ✅ | ✅ |
| `approved` | – | – | ✅ | – | – | – | – | ✅ | – |
| `dispatching` | – | – | – | ✅ | – | ✅ | – | ✅ | – |
| `running` | – | – | – | – | ✅ | ✅ | ✅ | ✅ | – |
| `failed` | – | – | ✅ (`retry_dispatch` only) | – | – | – | – | – | – |
| `timed_out` | – | – | ✅ (`retry_dispatch` only) | – | – | – | – | – | – |

Forbidden transitions (non-exhaustive, illustrative — anything not in the
matrix is forbidden by construction, enforced by `DispatchService` checking
membership before writing):

- `pending -> running` (must pass through `dispatching`; skipping it would
  mean a dispatch could be "running" without ever having been claimed by the
  adapter layer, breaking the audit trail's causality).
- `awaiting_approval -> dispatching` (must pass through `approved` — this is
  the literal enforcement of "no automatic approval," §6).
- Any transition **out of** `succeeded`/`cancelled`/`rejected` (fully
  terminal).
- `failed -> dispatching` or `timed_out -> dispatching` via any path other
  than `retry_dispatch()` (e.g. `synchronize_status()` may never resurrect a
  terminal-looking dispatch — it can only apply transitions already reachable
  from the *current* status in this matrix).

### Approval states

`requested`, `approved`, `rejected`, `cancelled`, `expired`.

| From \ To | approved | rejected | cancelled | expired |
|---|---|---|---|---|
| `requested` | ✅ (`approve`, authorized user only) | ✅ (`reject`) | ✅ (`expire_or_close`, dispatch cancelled) | ✅ (`expire_or_close`, `expires_at` passed) |

All four target states are terminal. There is no transition into `requested`
from anywhere except `ApprovalService.request_approval()`'s initial insert.

### Night Shift job → dispatch/approval mapping

`night_queue_jobs.status` (5A.1, unchanged, still authoritative for the
Night Shift domain) gains a nullable `dispatch_id` column (§9). Mapping:

| `night_queue_jobs.status` | Dispatch involvement |
|---|---|
| `queued` | No dispatch yet. `NightShiftWorker.claim_job()` acquires the lease; `execute_via_dispatch()` then calls `create_dispatch()`. |
| `preparing`, `validating` | Internal Night Shift pre-dispatch pipeline (5A.1, unchanged) — no `dispatch_id` yet; these represent work the job does *before* a dispatch is even created. |
| `draft_saved` | Still no `dispatch_id` — this is the 5A.1-defined point where a draft is ready and the job is about to become `awaiting_approval` on its own terms, independent of Wave 3. |
| `awaiting_approval` | `dispatch_id` present, dispatch status is `awaiting_approval`. `NightShiftWorker.defer_for_approval()` set this. |
| `completed` | `dispatch_id` present, dispatch status is `succeeded`. |
| `rejected` | `dispatch_id` present, dispatch status is `rejected`. |
| `failed_safely` | `dispatch_id` present (or absent, if the job never got as far as `create_dispatch()`), dispatch status is `failed`, `timed_out`, or `cancelled`. |

`JOB_TRANSITIONS` (5A.1, `app/nightshift/service.py`) is **not modified** —
Wave 3 adds a parallel `dispatch_id` linkage, it does not change what values
`night_queue_jobs.status` may take or how it moves between them.

### Stale-update protection

Every write method on `DispatchService`/`ApprovalService` uses
compare-and-swap on the current status (`UPDATE ... WHERE dispatch_id = ?
AND status = ?`), identical in shape to `ExecutionRepository.transition_state
(expected_state=...)`, `ControlTowerRepository.transition_work_item
(expected_status=...)`, and `NightShiftRepository.set_mode(expected_mode=...)`.
A CAS miss raises `StaleUpdateError` for direct caller-initiated transitions
(`approve`, `reject`, `cancel_dispatch`, `retry_dispatch`) but returns
`StatusSyncResult(applied=False, ...)` without raising for
`synchronize_status()`, since that method is expected to be called
speculatively/concurrently (§1).

### Transaction and audit requirements

Every state-changing call writes exactly one row to `dispatch_audit_log` or
`approval_audit` **in the same transaction** as the state write (mirroring
`ControlTowerRepository.create_work_item`'s single-transaction
insert+dependencies+audit pattern) — a state change with no audit row, or an
audit row with no corresponding state change, must never be possible.

---

## 5. Idempotency and duplicate protection

**Idempotency-key format:** caller-supplied opaque string. Recommended
convention (not enforced by type, only by the callers this contract
specifies): `"{source_type}:{source_id}:{purpose}"`, e.g.
`"night_shift_job:job-abc123:overnight_dispatch"` or
`"control_tower_work_item:9f2e...:document_agent_dispatch"`.

**Uniqueness constraint:** `UNIQUE(source_type, source_id, idempotency_key)`
on `dispatches`, enforced at the SQLite level — the same DB-level-not-just-
app-level discipline as `night_queue_jobs.UNIQUE(job_type, deduplication_key)`.

**`create_dispatch` replay behavior:** on a `UNIQUE` collision,
`DispatchService.create_dispatch()` compares the incoming
`agent_id`/`capability`/`payload_ref` against the existing row:
- If they match, it returns the **existing** `DispatchRecord` unchanged (safe
  replay — a double-tapped Telegram command or a retried HTTP-adjacent call
  must not create a second dispatch).
- If they differ, it raises `DuplicateDispatchError` (the caller asked for
  the same idempotency key with different content — a caller bug, not a safe
  retry).

This differs deliberately from `NightShiftService.enqueue_night_job()`,
which raises `DuplicateNightJobError` unconditionally on collision — dispatch
creation is a more frequently-retried operation (Telegram command retries,
worker crash-recovery) and benefits from replay-safety; Night Shift enqueue
is a rarer, worker-internal call where an unconditional raise is the correct,
already-proven behavior and is left untouched.

**Retry behavior:** `retry_dispatch()` always creates a **new**
`dispatch_attempts` row (`attempt_number = attempt_count + 1`) rather than
mutating the failed attempt in place, so the full attempt history is
reconstructable from `dispatch_attempts` alone.

**Duplicate Telegram command handling:** `/approve <id>` (or `/reject`)
issued twice by the same authorized user:
- If the approval is still `requested`, the second call processes normally.
- If the approval is already resolved to the **same** outcome the second
  call requests, the handler returns the existing `ApprovalDecision`
  (idempotent no-op reply — "Already approved.") rather than raising.
- If the approval is already resolved to a **different** outcome (e.g.
  `/reject` after `/approve` already succeeded), `StaleUpdateError` is
  raised and the handler replies with a sanitized "already resolved"
  message, not a stack trace (§11).

**Duplicate Night Shift pickup handling:** `claim_job()`'s lease acquisition
is `UPDATE night_queue_jobs SET lease_worker_id = ?, lease_expires_at = ? WHERE
job_id = ? AND status = 'queued' AND lease_worker_id IS NULL`. If two ticks
(or, in a future multi-worker scenario, two workers) race, exactly one
`UPDATE` affects a row; the loser gets `NightJobAlreadyClaimedError` and
moves on — no crash, no double-execution.

**Safe replay behavior, summary:** replay is safe at every layer that
matters for correctness (dispatch creation, approval decisions, job
claiming). Replay is explicitly *not* guaranteed safe for the underlying
agent action itself unless that action is approval-free (read-only or
draft-only, §6) — which is exactly why every approval-required action must
re-run through a fresh, explicit approval on each retry rather than being
silently re-executed.

**Exactly-once vs. at-least-once:**
- Dispatch **creation** is exactly-once per `(source_type, source_id,
  idempotency_key)`.
- Dispatch **execution** (the agent adapter call inside `dispatch()`) is
  at-least-once with an idempotent-replay guard at the creation boundary —
  a crash mid-run is recovered via `retry_dispatch()`, which is a distinct,
  audited, attempt-incrementing operation, never a silent re-run of the same
  attempt.
- Approval **decisions** are exactly-once per `approval_id` (CAS-protected;
  a second identical decision is a no-op reply, not a second write).

---

## 6. Approval policy

**Design note — resolving a tension in the brief:** the 5B.1 objectives say
"never … without explicit approval" for commits/pushes/merges/publishing/
messaging, which literally reads as *approval-required*, not
*prohibited*. This contract deliberately treats a narrower subset —
git mutations and secret changes — as **permanently prohibited** instead,
extending (not contradicting) that intent, for one grounded reason: the
existing `app/nightshift/classifier.py` `REGISTERED_JOBS` allowlist already
classifies `git_commit`/`git_push` as `PROHIBITED` (not
approval-eligible) precisely because a Telegram `/approve` tap from the
single authorized user is a materially weaker bar than a human deliberately
typing a git command in a terminal. Wave 3 keeps that existing bar rather
than lowering it. Everything else in the brief's list is treated as
literally approval-required, as written.

**Does not require approval (approval-free):**
- Read-only status/queries (`AgentCapability.capability == "read_only"`,
  mirroring `night_shift.classifier`'s `read_only` category, e.g.
  `execution_status_check`).
- Draft-only content preparation that is saved **locally only** and never
  transmitted anywhere (`capability == "draft_only"`, e.g.
  `draft_summary_prepare`) — mirrors `APPROVED_OVERNIGHT` in
  `app/nightshift/classifier.py` exactly; Wave 3 does not invent a new
  approval-free tier, it reuses this one.

**Always requires approval:**
- External communications: any Telegram message to a party other than the
  single authorized owner; any drafted-but-not-yet-sent email/WhatsApp
  content reaching the point of being marked "ready to send." (NOVA replying
  to the authorized owner in the same chat is normal bot UX, not an
  "external communication" — see §7's note on Telegram outbound.)
- Google Drive writes (create/update/delete) — Drive integration today
  (Sprint 5E) is read-only; Wave 3 introduces no Drive write capability into
  `app/google_workspace/drive/`, only a dispatch **classification** that
  would require approval if such a capability existed in a future sprint.
- Google Calendar writes — same: Sprint 5D is read-only; Wave 3 adds no
  Calendar write code, only forward-looking policy classification.
- Final/published document overwrite (a draft overwrite remains
  approval-free under `draft_only`; overwriting a document already marked
  final is not).
- Procurement or policy publication.
- Any financial commitment (purchase, subscription, invoice approval).
- Deferred-until-morning Night Shift jobs (`DEFERRED_UNTIL_MORNING` in
  `app/nightshift/classifier.py`, e.g. `dissertation_review_prepare`) —
  unchanged from 5A.1, now routed through `ApprovalService` instead of only
  `night_queue_jobs.status`.

**Permanently prohibited (no approval can authorize these — `AgentRegistry`
never registers a capability for them, and `app/nightshift/classifier.py`'s
`PROHIBITED` set is extended, additively, by 5F to make this explicit at the
Night Shift layer too):**
- Git staging, commit, push, merge, reset, rebase, or any other git mutation.
- Secret/credential changes.
- Destructive file operations (delete, move) — already `PROHIBITED` in
  `classifier.py`.
- Destructive database migrations — already `PROHIBITED`.

**No automatic approval, anywhere:** no code path in `ApprovalService` ever
transitions an approval to `approved` except `approve()`, and `approve()`
only succeeds when `approving_user_id == authorized_user_id`. `expire_or_close`
can only produce `expired`/`cancelled`, never `approved`. There is no
"auto-approve after N hours" behavior anywhere in this contract.

**Night Shift's additional floor:** even where 5B.1's dispatch-level policy
above would classify an action as approval-required (meaning a human
*could* approve it during the day), `app/nightshift/classifier.py`'s
`PROHIBITED` set is the **independent, additional** floor for anything a
Night Shift job attempts specifically — external communications,
Drive/Calendar mutation, purchases, and destructive operations are `PROHIBITED`
there regardless of dispatch-level approval status, because no human is
present overnight to grant a meaningful approval. §8 formalizes this as
"Night Shift never blocks-and-waits on an approval it cannot obtain
overnight."

---

## 7. Agent registry

`docs/AGENT_REGISTRY.md` currently has an empty "Active agents" section
(placeholder since Sprint 1.1). Sprint 5B.1 is the sprint that populates it,
using that document's own required-fields template.

| Agent ID | Display name | Category (matches `APPROVED_CATEGORIES`) | Capabilities |
|---|---|---|---|
| `document_agent` | Document Agent | `document` | `read_only`, `draft_only` |
| `presentation_agent` | Presentation Agent | `presentation` | `read_only`, `draft_only` |
| `procurement_agent` | Procurement Agent | `procurement` | `read_only`, `draft_only`, `external_communication` (approval-required), `paid_action` (prohibited — never registered) |
| `policy_agent` | Policy Agent | `policy` | `read_only`, `draft_only`, `publication` (approval-required) |
| `academic_agent` | Academic Agent | `academic` | `read_only`, `draft_only` |
| `development_agent` | Development Agent | `development` | `read_only`, `draft_only` — explicitly **no** `git_mutation` capability, ever (§6) |
| `workspace_agent` | Workspace Agent | `workspace`, `administrative`, `personal_planning` | `read_only`, `draft_only` |
| `night_shift_agent` | Night Shift Agent | `night_shift` | `read_only`, `draft_only` — the only agent `NightShiftWorker` is permitted to dispatch to for job types not otherwise routed to a category-specific agent |

Capability strings are deliberately coarse (`read_only`, `draft_only`,
`external_communication`, `publication`, `paid_action`) rather than
free-text, so `AgentRegistry.validate_capability()` and §6's policy table
can both key off the same closed vocabulary. `paid_action` and
`git_mutation` are listed here only to document that **no agent is ever
registered with them** — `AGENT_REGISTRY` simply never includes them in any
agent's `capabilities` frozenset, so `create_dispatch()` rejects them with
`UnsupportedCapabilityError` before any policy check even runs.

**Route validation:** `DispatchService.create_dispatch()` calls
`AgentRegistry.resolve_agent(agent_id)` then
`AgentRegistry.validate_capability(agent_id, capability)` before doing
anything else. An unresolvable `agent_id` raises `UnknownAgentError`; a
resolvable agent without the requested capability raises
`UnsupportedCapabilityError`. Both are terminal for that `create_dispatch`
call — no partial dispatch is ever created.

**Unknown-agent failure behavior:** fail closed. No dispatch row is written;
the caller (Telegram handler, Control Tower, or `NightShiftWorker`) receives
the typed error and is responsible for surfacing it as a sanitized message.

**No arbitrary shell/model/provider injection:** `agent_id` and `capability`
are validated against the closed, hardcoded `AGENT_REGISTRY` dict —
never used to construct a shell command, dynamic import path, or `getattr`
lookup. This mirrors `app/providers/registry.py`'s existing discipline
(`MODEL_REGISTRY` lookup, never a caller-supplied class path).

**How Claude/Codex/Gemini fit behind adapters:** `RegisteredAgent.adapter_id`
is a label (e.g. `"local_deterministic"`, `"claude_via_provider_gateway"`)
resolved, inside `app/dispatch/adapters.py`, to a concrete adapter class via
a small internal `dict[str, type[AgentAdapter]]` — never via a caller-
supplied string. `AgentAdapter` is a `typing.Protocol` with one method,
mirroring `app/providers/adapter.py`'s `ProviderAdapter` Protocol exactly:

```python
class AgentAdapter(Protocol):
    def run(self, dispatch_id: str, payload_ref: str, *, timeout_seconds: float) -> DispatchResult: ...
```

Wave 3 ships exactly one concrete adapter —
`LocalDeterministicAgentAdapter` (reusing `app/execution/adapter.py`'s
`LocalDeterministicAdapter` pattern: simulated, deterministic, no real model
call) — for every agent's `adapter_id` in `AGENT_REGISTRY`. Any future
adapter that calls a real model (Claude via `app/providers/`, Codex,
Gemini) is a **separate, later sprint's** responsibility: it must implement
`AgentAdapter`, be registered in the internal adapter map under a new
`adapter_id`, and go through `app/providers/`'s existing gateway/circuit-
breaker machinery for the actual network call — it never becomes a
user-controlled execution target, because the user only ever supplies
`agent_id`/`capability` (validated against the closed registry), never an
`adapter_id`, model name, or provider string directly.

---

## 8. Night Shift execution model

- **Job eligibility:** `status='queued'`, `dispatch_id IS NULL`,
  `eligible_after IS NULL OR eligible_after <= now`, runtime mode is
  `night_shift` (checked via `NightShiftService.get_runtime_mode()`).
- **Approval-free eligibility:** job's classified capability is `read_only`
  or `draft_only` (§6) — `execute_via_dispatch()` calls `dispatch()`
  immediately after `create_dispatch()`.
- **Approval-required deferral:** any other classification (or
  `DEFERRED_UNTIL_MORNING` per `classifier.py`, unchanged from 5A.1) routes
  through `defer_for_approval()` instead of `dispatch()`. The job's lease is
  released immediately — an `awaiting_approval` job is not "in progress" and
  must not occupy a concurrency slot (below) while it waits, possibly for
  hours, for the authorized user to act.
- **Worker claim/lease:** `claim_job()` acquires `(lease_worker_id,
  lease_expires_at = now + lease_seconds)` via CAS (§5). Default
  `lease_seconds = 600` (10 minutes) — long enough for one dispatch
  round-trip against the `LocalDeterministicAgentAdapter`'s
  `EXECUTION_TIMEOUT_SECONDS`-equivalent budget, short enough that a crashed
  worker's job is recoverable well within the night-shift window.
- **Concurrency limit:** the worker's `tick()`-driven loop claims and
  processes at most `N` jobs per tick (configurable, default 3) —
  `list_eligible_jobs(limit=N)`. This is a simple bounded-loop limit, not a
  thread/process pool; SQLite's single-writer model makes true parallel
  dispatch pointless here.
- **Timeout:** enforced inside the adapter call the same way
  `ExecutionService._dispatch()` enforces `EXECUTION_TIMEOUT_SECONDS` — if
  the adapter exceeds its budget, `DispatchService.dispatch()` transitions
  the dispatch to `timed_out`, and `record_result()` maps that to
  `night_queue_jobs.status = 'failed_safely'` (unless a retry is scheduled,
  below).
- **Retry/backoff:** on `failed`/`timed_out`, `NightShiftWorker` (not
  `DispatchService`) decides whether to call `retry_dispatch()` based on
  `NightQueueJob.attempt_count` (new column, §9) vs. a configured
  `max_attempts` (default 3, independent of `DispatchRequest.max_attempts`
  which caps retries at the dispatch layer — the worker's policy value must
  be `<=` the dispatch layer's cap). Backoff is a fixed delay
  (`eligible_after = now + backoff_seconds`, default 300s, applied by
  re-queuing rather than an in-process sleep) between attempts, so a failing
  job does not busy-loop across ticks.
- **Maximum attempts:** once `attempt_count >= max_attempts`, the worker
  calls `NightShiftService.transition_night_job(job_id, 'failed_safely')`
  instead of `retry_dispatch()`, and records a
  `notify(event_type="essential_service_repeated_failure", severity="critical", ...)`
  (existing 5A.1 method, existing `_CRITICAL_EVENT_TYPES` membership) so it
  surfaces immediately rather than waiting for the morning brief.
- **Cancellation:** `/nightqueue cancel <job_id>` (5F Telegram command) calls
  `NightShiftWorker`'s cancellation path, which calls
  `DispatchService.cancel_dispatch()` if a `dispatch_id` is attached, then
  `NightShiftService.transition_night_job(job_id, 'failed_safely')` (Night
  Shift has no separate `cancelled` job status — cancellation folds into the
  existing `failed_safely` terminal state, matching 5A.1's `JOB_TRANSITIONS`
  exactly, which is not modified).
- **Stale lease recovery:** each `tick()` also calls `recover_stale_job()`
  for every `queued`/lease-holding job whose `lease_expires_at < now` —
  handles the worker-process-crashed-mid-dispatch case described in §2's
  transaction-boundary note.
- **Quiet-hours notifications:** unchanged 5A.1 behavior
  (`record_notification_event`/`classify_notification`), reused as-is;
  5F's only addition is that dispatch-originated failures/successes now also
  produce notification events via the same existing severity classification
  (`informational` → morning brief, `attention_required` → prioritized
  morning brief, `critical` → immediate).
- **Morning brief generation:** unchanged mechanism
  (`generate_morning_brief`), 5F's addition is that the brief's "completed
  overnight"/"awaiting approval"/"failed safely" sections now reflect
  dispatch-linked jobs accurately (they already read `night_queue_jobs` by
  status, which is authoritative per §4's mapping table — no change to
  `generate_morning_brief()`'s query shape is required, only to the data
  now populating those statuses).
- **Failure isolation:** one job's dispatch failure never aborts the
  worker's loop over the remaining eligible jobs — each `claim_job`/
  `execute_via_dispatch`/`record_result` triple is wrapped so an exception
  from one job is caught, logged, and audited (`night_shift_audit_log`,
  event=`"job_isolated_failure"`) without stopping iteration over the rest
  of the batch.
- **Shutdown behavior:** on process shutdown (`app/main.py`'s
  `run_polling()` returning, or a future explicit stop), in-flight leases
  are **not** force-released by the worker itself — they are left to expire
  naturally and be picked up by `recover_stale_job()` on the next process
  start's first `tick()`, exactly mirroring `ExecutionService.initialize()`'s
  existing restart-reconciliation pattern (`reconcile_running_to_failed_with_ids`)
  but implemented as lease-expiry rather than a blanket state sweep, because
  Night Shift jobs (unlike single-process executions) must survive being
  claimed by a *different* worker instance after restart.

**Night Shift must use the 5B.1 dispatch interface, not its own executor:**
enforced structurally — `app/nightshift/worker.py` imports
`DispatchService`/`ApprovalService` from `app/dispatch/` and contains no
adapter invocation, no agent capability check, and no `dispatches`/
`approvals` table access of its own. A test (§13) asserts this by grepping
`app/nightshift/worker.py` for the absence of any `sqlite3`/raw-SQL access
to `dispatches`/`dispatch_attempts`/`approvals`/`approval_audit`.

---

## 9. Schema plan

All tables are additive (`CREATE TABLE IF NOT EXISTS`), owned by
`app/dispatch/schema.py` (5B.1) except the `night_queue_jobs` column
additions (5F, via the guarded `ALTER TABLE ... ADD COLUMN` pattern
`app/control_tower/schema.py`'s `_AUDIT_ADDITIONS` already established —
the only precedent in the repo for adding a column to an already-shipped
table, reused here rather than reinvented).

```sql
-- app/dispatch/schema.py (5B.1)

CREATE TABLE IF NOT EXISTS dispatches (
    dispatch_id     TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL
                    CHECK (source_type IN
                        ('control_tower_work_item','night_shift_job','telegram_direct')),
    source_id       TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    capability      TEXT NOT NULL,
    payload_ref     TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL,
    correlation_id  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN (
                        'pending','awaiting_approval','approved','dispatching',
                        'running','succeeded','failed','cancelled','rejected','timed_out'
                    )),
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    requested_by    TEXT NOT NULL,
    result_summary  TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(source_type, source_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_dispatches_status ON dispatches(status);
CREATE INDEX IF NOT EXISTS idx_dispatches_source ON dispatches(source_type, source_id);

CREATE TABLE IF NOT EXISTS dispatch_attempts (
    id              INTEGER PRIMARY KEY,
    dispatch_id     TEXT NOT NULL REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
    attempt_number  INTEGER NOT NULL,
    status          TEXT NOT NULL
                    CHECK (status IN ('running','succeeded','failed','timed_out','cancelled')),
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    result_summary  TEXT NOT NULL DEFAULT '',
    UNIQUE(dispatch_id, attempt_number)
);
CREATE INDEX IF NOT EXISTS idx_dispatch_attempts_dispatch_id ON dispatch_attempts(dispatch_id);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id      TEXT PRIMARY KEY,
    dispatch_id      TEXT NOT NULL REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
    requested_action TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'requested'
                     CHECK (status IN ('requested','approved','rejected','cancelled','expired')),
    requested_by     TEXT NOT NULL,
    resolved_by      TEXT,
    requested_at     TEXT NOT NULL,
    resolved_at      TEXT,
    expires_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_dispatch_id ON approvals(dispatch_id);

CREATE TABLE IF NOT EXISTS approval_audit (
    id          INTEGER PRIMARY KEY,
    approval_id TEXT NOT NULL REFERENCES approvals(approval_id) ON DELETE CASCADE,
    event       TEXT NOT NULL,
    actor       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dispatch_audit_log (
    id             INTEGER PRIMARY KEY,
    dispatch_id    TEXT NOT NULL REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
    event          TEXT NOT NULL,
    actor          TEXT NOT NULL,
    from_status    TEXT,
    to_status      TEXT,
    correlation_id TEXT,
    detail         TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dispatch_audit_dispatch_id ON dispatch_audit_log(dispatch_id);

CREATE TABLE IF NOT EXISTS dispatch_leases (
    dispatch_id       TEXT PRIMARY KEY REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
    worker_id         TEXT NOT NULL,
    leased_at         TEXT NOT NULL,
    lease_expires_at  TEXT NOT NULL
);
```

```sql
-- app/nightshift/schema.py additive column block (5F), guarded exactly like
-- app/control_tower/schema.py's _AUDIT_ADDITIONS (PRAGMA table_info check
-- before each ALTER TABLE ... ADD COLUMN, never applied to a column that
-- already exists):

ALTER TABLE night_queue_jobs ADD COLUMN dispatch_id TEXT;
ALTER TABLE night_queue_jobs ADD COLUMN lease_worker_id TEXT;
ALTER TABLE night_queue_jobs ADD COLUMN lease_expires_at TEXT;
ALTER TABLE night_queue_jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_night_queue_jobs_dispatch_id ON night_queue_jobs(dispatch_id);
CREATE INDEX IF NOT EXISTS idx_night_queue_jobs_lease ON night_queue_jobs(lease_expires_at);
```

**No separate `idempotency_keys` table.** Duplicate protection is enforced
directly by `dispatches.UNIQUE(source_type, source_id, idempotency_key)`,
consistent with the existing `night_queue_jobs.UNIQUE(job_type,
deduplication_key)` precedent — the repo has no separate migrations/version-
tracking table anywhere, and Wave 3 does not introduce the first one.

**Status synchronization** needs no dedicated table: `synchronize_status()`
performs a CAS `UPDATE` against `dispatches.status` and writes its result to
`dispatch_audit_log`; `StatusSyncResult` is computed, not stored.

**Leases** live on `dispatch_leases` (dispatch-level) and the four new
`night_queue_jobs` columns (job-level) — two separate lease concepts kept
deliberately distinct: a dispatch lease exists only transiently for the
duration of one `dispatch()` call and is not currently required by this
contract's interfaces (reserved for a future multi-worker scenario;
`dispatch_leases` is created now, additively, so that future sprint does not
need another schema migration) — the operative lease for Wave 3 is the
**job-level** lease on `night_queue_jobs`, since that is what prevents two
worker ticks from claiming the same job.

**Preserving all current data / no destructive migrations:** every
statement above is `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT
EXISTS`, or a column-existence-guarded `ALTER TABLE ... ADD COLUMN`. No
`DROP`, `ALTER ... RENAME`, or data-mutating statement appears anywhere in
this plan. Both `DispatchService.initialize()` and the `night_shift` schema
addition must be safe to run twice (tested, §13), matching every existing
domain's `initialize()` contract.

---

## 10. Error taxonomy

All defined in `app/dispatch/errors.py`, inheriting a common
`DispatchError(RuntimeError)` root (mirroring `NightShiftError`/
`ControlTowerError`'s existing per-domain root-exception pattern).

| Error | Raised by | Retryable? |
|---|---|---|
| `InvalidRequestError` | `create_dispatch` (malformed `source_type`, empty `agent_id`, oversized/invalid fields) | No — caller must fix the request |
| `UnknownAgentError` | `create_dispatch`, `AgentRegistry.resolve_agent` | No |
| `UnsupportedCapabilityError` | `create_dispatch`, `AgentRegistry.validate_capability` | No |
| `ApprovalRequiredError` | `dispatch()` called while status is `awaiting_approval` | No — caller must go through `ApprovalService` first |
| `ApprovalRejectedError` | Surfaced when a caller inspects a dispatch whose linked approval was rejected | No |
| `DuplicateDispatchError` | `create_dispatch` (idempotency-key collision, differing content) | No — caller bug |
| `StaleUpdateError` | Any CAS-guarded transition whose `expected_status` no longer matches | **Yes** — caller should re-fetch current status and retry the intended transition if still applicable |
| `DispatchUnavailableError` | `dispatch()`/adapter layer cannot be reached (e.g. future real-model adapter's transport failure) | **Yes** |
| `ExecutionFailureError` | Wraps an adapter-reported failure (`DispatchResult.success is False`) | Caller-dependent — **yes** via `retry_dispatch()` up to `max_attempts` |
| `DispatchTimeoutError` | Adapter exceeds its timeout budget | **Yes**, same as above |
| `CancellationError` | `cancel_dispatch()` called on an already-terminal dispatch | No |
| `RetryExhaustedError` | `retry_dispatch()` called after `attempt_count >= max_attempts` | No |
| `NightShiftIneligibleError` | `NightShiftWorker` methods, job fails eligibility checks (§8) | No |
| `LeaseConflictError` (`NightJobAlreadyClaimedError` for jobs, dispatch-level reserved for future use) | `claim_job()` CAS miss | No — a concurrent claimant won; caller moves to the next eligible job |
| `ApprovalAuthorizationError` | `approve()`/`reject()` by a non-authorized user | No |
| `ApprovalNotFoundError` / `DispatchNotFoundError` | `get_approval`/`get_dispatch` on an unknown ID | No |
| `InternalDispatchError` | Anything genuinely unexpected (DB error not otherwise categorized) | No — surfaced generically, logged, never retried automatically |

Every error message is a fixed, sanitized string (or built only from
already-sanitized fields like `dispatch_id`/`status`) — never interpolates
raw exception text, stack traces, or adapter output, matching
`ExecutionService`'s `_SENSITIVE_REJECTION_MESSAGE` convention and
`app/security.SENSITIVE_CONTENT_PATTERN` guard reused throughout.

---

## 11. Security constraints

- **No secrets in payloads, logs, or audit.** `payload_ref` is a hash/label,
  never raw content (mirrors `executions.instruction_hash`). Every free-text
  field (`result_summary`, `requested_action`, `reason`, audit `detail`) is
  passed through `SENSITIVE_CONTENT_PATTERN` before storage, exactly like
  every existing domain.
- **No raw provider/agent responses stored or returned.** `DispatchResult.summary`
  is a bounded, sanitized string — never the adapter's raw output object.
  This is enforced today for the one shipped adapter
  (`LocalDeterministicAdapter`'s simulated output) and is a **hard
  requirement** on any future real-model adapter (§7).
- **No arbitrary shell command input anywhere.** No component introduced by
  this contract calls `subprocess`, `os.system`, or any shell — verified by
  the same monkeypatch-`subprocess.Popen`/`os.system`-raises test pattern
  `tests/test_execution_security.py` established and
  `docs/SPRINT_5A1.md`'s acceptance criteria already reused once.
- **Sandboxed execution.** The one shipped adapter is deterministic/
  simulated, with no filesystem write outside the SQLite DB and no network
  call — identical sandboxing to `app/execution/adapter.py`.
- **No repository writes unless explicitly approved.** No agent capability
  in `AGENT_REGISTRY` (§7) includes filesystem write outside the SQLite DB.
- **No Git mutation by application code, ever** — not even behind an
  approval gate (§6's design note).
- **No real external actions in tests.** `tests/test_dispatch_*.py` and
  `tests/test_nightshift_worker.py` use the same mocking discipline already
  established for Drive/Calendar (`tests/google_workspace/`): fresh
  `MemoryDatabase(tmp_path / ...)` per test, a fake/deterministic
  `AgentAdapter` (never the real network-capable kind, which does not exist
  yet in Wave 3 anyway), no real Telegram transport (reuse the
  `FakeMessage`/`FakeUpdate` pattern from `test_telegram_control_tower.py`).
- **Safe correlation and audit metadata only.** `correlation_id` and audit
  `metadata`/`detail` fields follow the same closed-key discipline as
  `NightShiftService._metadata()` (`{"prompt","content","document","response","exception"}`
  disallowed as top-level keys) — extended, not reinvented, for
  `dispatch_audit_log`/`approval_audit`.
- **Fail closed.** Every ambiguous or unrecognized input (unknown agent,
  unsupported capability, unregistered job type, expired lease with no
  clear recovery path) raises a typed error and leaves state unchanged,
  never defaults to proceeding.

---

## 12. Parallel file ownership

| File | Owner | Notes |
|---|---|---|
| `app/dispatch/__init__.py`, `service.py`, `approvals.py`, `registry.py`, `models.py`, `repository.py`, `schema.py`, `errors.py`, `adapters.py` | **5B.1 exclusive** | New. |
| `tests/test_dispatch_*.py` | **5B.1 exclusive** | New. |
| `docs/SPRINT_5B1.md`, `docs/agent-dispatch-and-approvals.md` (new operational doc, mirroring `docs/executive-control-tower.md`'s style) | **5B.1 exclusive** | New. |
| `docs/AGENT_REGISTRY.md` | **5B.1 exclusive edit** | Currently an empty template (Sprint 1.1) — 5B.1 populates its "Active agents" table per §7. |
| `app/nightshift/worker.py` | **5F exclusive** | New. |
| `app/nightshift/schema.py` | **5F exclusive edit** | Additive column block only (§9); 5A.1's existing tables untouched. |
| `app/nightshift/classifier.py` | **5F exclusive edit** | Additive `PROHIBITED` entries only (`git_merge`, `git_reset`, `git_rebase`, `telegram_outbound_message`), per §6. |
| `app/nightshift/models.py`, `app/nightshift/repository.py` | **5F exclusive edit** | Additive fields/queries for the four new `night_queue_jobs` columns and lease queries. |
| `tests/test_nightshift_worker.py`, `tests/test_nightshift_automation.py` | **5F exclusive** | New. |
| `docs/SPRINT_5F.md` | **5F exclusive** | New. |
| `docs/night-shift-runtime.md` | **5F exclusive edit** | Existing 5A.1 doc — 5F appends the automation sections; does not rewrite the foundation sections. |
| `app/main.py` | **Shared, ordered** — see below | |
| `app/telegram_bot.py` | **Shared, ordered** — see below | |
| `docs/CURRENT_SPRINT.md` | **Shared, sequential** — 5B.1 first, 5F appends after rebase | |
| `app/control_tower/service.py` | **Pre-approved narrow edit, 5B.1 only** | `list_approvals()` gains a third source (`self.approvals.list_pending()`, if an `ApprovalService` was injected) — additive `elif`/extra loop, same shape as its existing execution/night_shift union blocks. `ControlTowerService.__init__` gains one new optional keyword-only param, `approvals: ApprovalService | None = None`, matching the existing `execution`/`night_shift` optional-collaborator pattern exactly. **5F does not touch this file.** |
| `app/control_tower/repository.py`, `models.py`, `schema.py` | **Neither sprint touches these** | Control Tower's own state machine and tables are out of scope. |
| `app/execution/**` | **Neither sprint touches these** | Dispatch is a parallel, separate lifecycle (§3 design note) — `executions` table and `ExecutionService` are untouched. |
| `app/google_workspace/**` (Calendar, Drive) | **Neither sprint touches these** | Both remain read-only; Wave 3 adds no write scopes, no new OAuth scopes, no new Google API calls. |
| `app/memory/**`, `app/router/**`, `app/providers/**` (except being *read* as a pattern reference), `app/dissertation/**`, `app/security.py`, `app/config.py`, `app/run_singleton.py` | **Neither sprint touches these** | No new settings are needed — Wave 3 reuses `NOVA_MEMORY_DB_PATH` and `settings.telegram_allowed_user_id`. |

**`app/main.py` — exact insertion order** (current file is 130 lines,
read in full during grounding; line numbers below refer to that baseline
and will shift slightly as blocks are inserted — insert **in this order**,
each as its own block, mirroring the existing `construct -> try: initialize()
except MemoryDatabaseError -> return 1` shape used by every prior addition):

1. *(unchanged)* memory → dissertation → execution → night_shift blocks (lines 66–92).
2. **5B.1 inserts here**, immediately after the `night_shift` block and
   *before* the `control_tower` block: construct `AgentRegistry` (no DB, no
   `initialize()` — it's a static import), then
   `ApprovalService(MemoryDatabase(...), authorized_user_id=settings.telegram_allowed_user_id)`
   → `.initialize()`, then
   `DispatchService(MemoryDatabase(...), registry=AGENT_REGISTRY_MODULE, approvals=approval_svc)`
   → `.initialize()`.
3. *(existing `control_tower` block, lines 95–104)* — 5B.1 additively passes
   `approvals=approval_svc` into the existing `ControlTowerService(...)` call
   (the one pre-approved edit to that block; `execution=`/`night_shift=`
   kwargs are untouched).
4. **5F inserts here**, immediately after the (now `approvals`-aware)
   `control_tower` block and *before* the `provider_svc` block: construct
   `NightShiftWorker(MemoryDatabase(...), night_shift=night_shift,
   dispatch=dispatch_svc, approvals=approval_svc, worker_id="nightshift-1")`
   — no separate `.initialize()` call (it owns no schema of its own; it
   reads/writes through the services it wraps).
5. *(existing `provider_svc` block, unchanged)*.
6. `build_application(settings, memory, execution_svc, provider_svc,
   night_shift, control_tower, dispatch_svc, approval_svc, night_worker)` —
   5B.1 adds `dispatch_svc`/`approval_svc` params first (its merge); 5F
   rebases and adds `night_worker` after.

**`app/telegram_bot.py` — exact insertion order:**

1. 5B.1 extends `build_application()`'s signature
   (`dispatch: DispatchService | None = None, approvals: ApprovalService | None
   = None`), adds matching `bot_data[...]` entries and private accessor
   functions (`_dispatch_svc`, `_approval_svc`, matching the existing
   `_control_tower` accessor shape exactly), appends its command handlers
   (`/dispatch`, `/dispatches`, `/dispatchstatus`, `/approve`, `/reject`,
   `/canceldispatch`, `/retrydispatch`) after the existing Control Tower
   command block, and extends `HELP_MESSAGE`.
2. 5F rebases onto merged 5B.1, then extends `build_application()`'s
   signature again (`night_worker: NightShiftWorker | None = None`), adds
   `bot_data["night_worker"]`/`_night_worker` accessor, appends its command
   handlers (`/nightshift`, `/nightstatus`, `/nightqueue`, `/wake`) after
   5B.1's block, and extends `HELP_MESSAGE` again.

This ordering is exactly why §14 requires 5B.1 to merge before 5F begins its
own `telegram_bot.py`/`main.py` edits in earnest — 5F's insertion points are
defined *relative to* 5B.1's, not to the current baseline.

---

## 13. Tests

### Sprint 5B.1

- Dispatch creation: valid request → `pending` (approval-free) or
  `awaiting_approval` (approval-required), per §6 classification.
- Idempotency: identical `(source_type, source_id, idempotency_key)` +
  identical content → same `DispatchRecord` returned, no second row;
  identical key + differing content → `DuplicateDispatchError`.
- Agent validation: unknown `agent_id` → `UnknownAgentError`, no row
  written.
- Capability validation: known agent, unsupported capability →
  `UnsupportedCapabilityError`; `paid_action`/`git_mutation` never present
  in any agent's capability set (assert against `AGENT_REGISTRY` directly).
- Approval request: `request_approval` is idempotent while a `requested`
  approval is open; a second call while already resolved raises/returns per
  §5.
- Approve/reject: only `authorized_user_id` may `approve`/`reject`; wrong
  user → `ApprovalAuthorizationError`; approving a non-`requested` approval
  → `StaleUpdateError` unless it's an idempotent replay of the same
  decision.
- Forbidden action handling: attempting to register or dispatch a
  `git_mutation`/`paid_action`-capability request fails at
  `UnsupportedCapabilityError`, before any approval logic runs.
- Cancellation: from every non-terminal status → `cancelled`; from a
  terminal status → `CancellationError`.
- Retry: `retry_dispatch` from `failed`/`timed_out` creates a new
  `dispatch_attempts` row and increments `attempt_count`; blocked once
  `attempt_count >= max_attempts` with `RetryExhaustedError`.
- Status sync: `synchronize_status` applies only when `expected_status`
  matches current status; a stale call returns `applied=False` without
  raising and without writing.
- Telegram command registration: each new command (`/dispatch`,
  `/dispatches`, `/dispatchstatus`, `/approve`, `/reject`,
  `/canceldispatch`, `/retrydispatch`) registered exactly once — same
  walk-`application.handlers`-and-assert pattern as
  `test_build_application_registers_each_control_tower_command_once`.
- Error sanitization: every raised error's `str()` and every audit `detail`
  is checked against the existing `_SENSITIVE_INPUTS` parametrized list —
  none leak.
- Migration idempotency: `DispatchService.initialize()` (and
  `ApprovalService.initialize()`) callable twice without error or
  duplication.
- Audit integrity: every state-changing call produces exactly one
  `dispatch_audit_log`/`approval_audit` row in the same transaction as the
  state write (assert both succeed or both roll back together, e.g. by
  forcing a failure between them in a test double and confirming neither
  persisted).
- No external calls: `LocalDeterministicAgentAdapter` makes no network call
  (reuse the `subprocess.Popen`/`os.system`-raises monkeypatch pattern).

### Sprint 5F

- Eligibility: `list_eligible_jobs` returns only `queued`, un-leased,
  `eligible_after`-satisfied jobs, and only when runtime mode is
  `night_shift`.
- Approval deferral: a `DEFERRED_UNTIL_MORNING`/non-approval-free job routes
  through `defer_for_approval`, never through `dispatch()` directly; its
  lease is released immediately after deferral.
- Worker claim: `claim_job` CAS succeeds exactly once per job.
- Duplicate claim protection: a second `claim_job` on an already-leased job
  → `NightJobAlreadyClaimedError`.
- Dispatch adapter use: `execute_via_dispatch` calls
  `DispatchService.create_dispatch`/`dispatch` — never writes to
  `dispatches`/`approvals` directly (grep-based structural test, §8).
- Retry/backoff: failed job with `attempt_count < max_attempts` → re-queued
  with `eligible_after` in the future, not immediately re-claimable.
- Timeout: a job whose dispatch times out maps to `failed_safely` (or
  retried, per attempt count) — never left dangling in `running`.
- Cancellation: `/nightqueue cancel` cancels the linked dispatch (if any)
  and transitions the job to `failed_safely`.
- Stale lease recovery: a job with `lease_expires_at` in the past is
  recovered by the next `tick()` — cancels any in-flight dispatch, releases
  the lease, and either re-queues or fails safely per attempt count.
- Failure isolation: one job raising inside the worker's per-job loop does
  not prevent the remaining eligible jobs in the same tick from being
  processed.
- Quiet-hours routing: dispatch-originated notification events route
  through the existing `classify_notification`/`_CRITICAL_EVENT_TYPES`
  severity logic unchanged.
- Morning brief: reflects dispatch-linked job outcomes correctly across all
  three brief sections (completed / awaiting approval / failed safely).
- Shutdown: no lease is force-cleared on shutdown; a job claimed before
  shutdown is picked up by `recover_stale_job()` on the next `tick()` after
  restart (simulated by constructing a fresh `NightShiftWorker` instance
  against the same DB in the test).
- No autonomous prohibited action: a full-suite assertion that no job in
  `REGISTERED_JOBS` with `disposition == PROHIBITED` can ever reach
  `execute_via_dispatch` (the existing `ProhibitedNightJobError` path,
  now also asserted from the worker's entry point, not just
  `enqueue_night_job`).
- No duplicate dispatch system: the structural grep-based test from §8,
  confirming `app/nightshift/worker.py` contains no direct
  `dispatches`/`approvals` table access.
- Full regression compatibility: all pre-Wave-3 tests (392) plus all new
  5B.1 and 5F tests pass together.

---

## 14. Merge and validation order

1. **Parallel implementation.** 5B.1 and 5F are implemented on separate
   branches, each against the Wave 2 baseline (`5b60e75`). 5F's branch may
   read this contract's §2 interface stubs to write its own tests against a
   local stand-in, but must not fork or duplicate `app/dispatch/` code.
2. **Claude reviews each branch independently** against its own sprint spec
   (`docs/SPRINT_5B1.md`, `docs/SPRINT_5F.md`) and this contract.
3. **Merge 5B.1 first** (it is the integration owner, §1 — 5F's interface
   dependency runs one direction only).
4. **Run full regression** on `main` post-5B.1-merge (392 pre-existing +
   5B.1's new tests).
5. **Revalidate 5F against the actual merged 5B.1 interface** — 5F's branch
   rebases onto merged `main`, replaces any local stand-in with the real
   `app/dispatch/` import, and re-runs its own test suite against the real
   `DispatchService`/`ApprovalService` (not just its own mocks) before
   proceeding. This step exists specifically because §12's `main.py`/
   `telegram_bot.py` insertion points are defined relative to 5B.1's actual
   merged shape, not this contract's draft.
6. **Merge 5F.**
7. **Full Wave 3 regression** — 392 pre-existing + 5B.1 tests + 5F tests, all
   green together on `main`.
8. **Push only after all tests pass**, and only with explicit user
   confirmation (per this contract's own constraint: no autonomous push).

---

## 15. Sprint specifications

Full specs: `docs/SPRINT_5B1.md`, `docs/SPRINT_5F.md`. This contract
(`docs/WAVE_3_INTEGRATION_CONTRACT.md`) is the binding cross-sprint
reference both specs point back to for anything defined here rather than
duplicated there (interfaces, DTOs, schema, state machines, file ownership).

---

## Final architecture verdict

**READY FOR PARALLEL WAVE 3**

Both sprints have a single, unambiguous integration seam
(`app/dispatch/`, owned by 5B.1) that 5F consumes and never duplicates; the
only genuinely shared files (`app/main.py`, `app/telegram_bot.py`,
`docs/CURRENT_SPRINT.md`) have an explicit, ordered insertion plan (§12) and
an explicit merge sequence that resolves the one real dependency direction
(§14). The approval-fragmentation problem identified in grounding research
is resolved by designating `ApprovalService` the canonical authority rather
than adding a fourth parallel implementation. No destructive migrations, no
new external write capability, and no automatic-approval path exist
anywhere in this design.
