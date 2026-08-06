# Sprint 5A.1 — Night Shift Runtime Foundation

## Status: Implemented (Sprint 5A.1 foundation)

## Objective

Give NOVA a safe way to operate quietly overnight: a persisted runtime mode,
a timezone-aware schedule, a classified job queue that only ever runs
read-only/repeatable/reversible/draft-only work automatically, a
fail-closed critical-notification path, and one consolidated morning brief —
without executing anything destructive, external, or model-driven, and
without touching Sprint 5A's launchd service, lock, or logging.

## Implementation Record

The implemented foundation uses the additive tables `runtime_mode_state`,
`night_shift_configuration`, `night_queue_jobs`, `night_notification_events`,
`morning_briefs`, and `night_shift_audit_log`. Earlier architecture labels in
this proposal (`nightshift_*`) are draft names only; the runtime documentation
in `docs/night-shift-runtime.md` is the operational source of truth. Startup
initializes the schema but intentionally registers no executor loop.

---

## Grounding in what actually exists today

This sprint builds on the *real* Sprint 5A/5C/6A.0 artifacts already merged
(`de92fb2`, `f932c74`, `80c4ab5`), not a hypothetical design:

- **Runtime**: `app/run_singleton.py` wraps `app.main.main()` with an
  `fcntl` exclusive lock on `data/nova.lock`. `app/main.py::configure_logging()`
  installs a `RotatingFileHandler` (5MB × 3 backups) on `data/nova.log`.
  Health today is PID-liveness only, checked by `scripts/service.sh health`
  — there is no application-level heartbeat (Sprint 5A's own Accepted
  Limitation #2). **This sprint does not touch `run_singleton.py`,
  `scripts/service.sh`, or `configure_logging()`.**
- **Service wiring pattern**: `app/main.py::main()` constructs each service
  as `Service(MemoryDatabase(settings.nova_memory_db_path), ...)`, calls
  `.initialize()` inside a try/except, then passes it to `build_application()`.
  This sprint follows the identical pattern — one more block, same shape.
- **Google**: the real module is `app/google_workspace/` (`auth.py`,
  `factory.py`, `scopes.py`), not a hypothetical `app/google/`.
  `requirements.txt` already has `google-auth`, `google-auth-oauthlib`,
  `google-api-python-client` pinned.
- **Dissertation**: `app/dissertation/` exists with a chapter/subchapter/
  document-version/paragraph-map/review-job/revision-log model, matching the
  Sprint 6A.0 foundation design.

---

## Scope

### 1. Persistent runtime modes

Four modes, restart-safe (persisted, not in-memory only):

| Mode | Meaning |
|---|---|
| `active` | Normal daytime operation — identical to today's behavior. |
| `night_shift` | Scheduled quiet window: safe work runs automatically per §4; unsafe work is queued for morning approval; notifications suppressed except critical. |
| `quiet` | Manual "do not disturb" override: **nothing** is processed automatically, not even approved-overnight categories — everything is queued untouched until mode changes. Distinct from `night_shift`: this is an explicit human override, not a schedule. |
| `maintenance` | Manual override for NOVA's own upkeep (e.g. before a migration): new job intake is paused; existing state is left alone; distinct from `quiet` in *intent* (operational stability, not "user asleep"), same behavior (no automatic processing). |

### 2. Night-shift schedule

- Configurable `start_time`, `end_time`, `morning_brief_time` (all
  `HH:MM` local time).
- Timezone-aware; default `Asia/Jakarta`, configurable per-deployment.
- Safe midnight-crossing: `start_time=22:00, end_time=06:00` is a normal,
  correctly-handled case (the window wraps past midnight).
- Restart-safe: schedule config and current mode are both read from SQLite
  at startup, not recomputed from scratch — a restart mid-night-shift stays
  in `night_shift`, does not silently reset to `active`.
- The automatic scheduler **only ever transitions `active` ↔ `night_shift`**.
  If the operator has manually set `quiet` or `maintenance`, the scheduler
  does not override it — it must be manually returned to `active` first.
  This is a deliberate design choice: an explicit override always outranks
  the clock.

### 3. Night queue

- Persistent queued-job metadata only — no raw payload content (see §10).
- A static **job-type registry** (mirroring `app/providers/registry.py`'s
  pattern) classifies every `job_type` string into exactly one of:
  `approved_overnight`, `deferred_until_morning`, `critical_notify_only`,
  or `prohibited`.
- **Any `job_type` not present in the registry is rejected outright** — never
  queued, never run. This is a hard allowlist, not a denylist.
- `prohibited` job types are explicitly registered as such (not merely
  absent) so a rejection is auditable as "prohibited category," distinct
  from "unknown/unregistered type" — both are rejected, but for a clearly
  different, loggable reason.

### 4. Safety policy

- Only work classified `read_only`, `repeatable`, `reversible`, or
  `draft_only` may execute automatically during `night_shift`.
- Anything `destructive` or `external` always waits for explicit morning
  approval — never runs automatically, regardless of mode.
- **Categorically prohibited, always, in every mode** (not merely deferred):
  `git add/commit/push/merge/reset/rebase`; sending email, WhatsApp, or any
  external message; Calendar mutations; file deletion or moves; overwriting
  an approved document; changing secrets/credentials; purchases; destructive
  migrations. These job types either don't exist in the registry or are
  registered with `category=prohibited` — there is no approval path that
  unlocks them from *this* sprint's queue; unlocking any of them is a
  decision for a future, explicitly-scoped sprint, not a runtime toggle here.
- Execution lifecycle for approved-overnight work:
  `prepare → validate → save_draft → await_approval`. This sprint defines
  and persists these four states; it does not implement a real executor for
  any job type (see Out of scope, and §7's executor-registration hook for
  future sprints).

### 5. Notification policy

| Severity | Trigger examples | Delivery |
|---|---|---|
| `informational` | Ordinary completions, expected no-ops | Morning brief only |
| `attention_required` | A deferred/queued item, a draft ready for review | Morning brief, prioritized section |
| `critical` | Security risk, probable data loss, database corruption, repeated failure of an essential service | Routed for **immediate** delivery (this sprint persists the routing decision; actual Telegram send is Sprint 5B's job — see §7) |

Explicitly **not** critical: failing tests, provider rate limits, ordinary
document-processing errors — these are `informational` or
`attention_required` at most.

**Fail closed**: if severity cannot be confidently classified, or the
routing logic itself raises, the event defaults to `critical`/immediate
rather than being silently dropped or downgraded. For a notification system,
fail-closed means *err toward notifying*, not toward silence.

### 6. Morning brief foundation

One consolidated record per morning (not one notification per event), with
five sections: `completed_overnight`, `attention_required`,
`awaiting_approval`, `failed_safely`, `runtime_health_summary`. No sensitive
content or raw error body in any section — only sanitized summaries and
references (job IDs, categories, counts), matching the redaction discipline
already established for `/providerstatus` and `/runstatus`.

### 7. Runtime integration

- Layered strictly on top of the existing `app/main.py` wiring — one more
  `Service(...)` block, same shape as Memory/Dissertation/Execution/Provider.
- Does not modify `app/run_singleton.py`, `scripts/service.sh`, or
  `configure_logging()`.
- Needs a periodic in-process tick (schedule-boundary checks, morning-brief
  generation) that must fire even with zero incoming Telegram messages
  overnight. **Recommended mechanism**: `python-telegram-bot`'s built-in
  `application.job_queue.run_repeating(...)`, which requires changing
  `requirements.txt`'s `python-telegram-bot>=22.0,<23.0` to
  `python-telegram-bot[job-queue]>=22.0,<23.0` (same package, the
  `job-queue` extra transitively adds `APScheduler`). This is the one new,
  tightly-scoped dependency change for this sprint — chosen over a
  hand-rolled `asyncio` polling loop because it's the officially-supported
  mechanism for exactly this use case and avoids reimplementing
  graceful-shutdown semantics against `run_polling()`'s own event loop.
- **Interfaces reserved for Sprint 5B (Telegram Agent Operations)**:
  `NightShiftService.set_mode()`, `.get_status()`, `.list_queue()`,
  `.approve_queued_job()`, `.reject_queued_job()`,
  `.get_latest_morning_brief()`, `.wake_now()` map directly to the four
  future commands in §8.
- **Interface reserved for a future "Full Night Shift Automation" sprint**:
  `register_job_executor(job_type: str, executor: JobExecutor) -> None` —
  lets a future sprint plug in a real executor for an `approved_overnight`
  job type without this sprint needing to know what that executor does.
  Calling this with a `job_type` not already in the registry, or one
  registered as `prohibited`, raises — the executor registry cannot be used
  to smuggle in a prohibited category.

### 8. Commands/interfaces

Telegram commands are **specified as future contracts only** — not
implemented this sprint:

```text
/nightshift <mode>   → NightShiftService.set_mode(mode, actor, reason)
/nightstatus          → NightShiftService.get_status()
/nightqueue            → NightShiftService.list_queue()
/wake                   → NightShiftService.wake_now(actor)
```

`app/telegram_bot.py` is **not touched** by this sprint.

---

## Architecture

```text
app/main.py: main()
    │
    ├── ...existing Memory / Dissertation / Execution / Provider wiring (unchanged)
    │
    └── NightShiftService(MemoryDatabase(...), settings.nova_timezone or default)
            │  .initialize()  ← additive schema
            │
            ▼
    application.job_queue.run_repeating(nightshift_tick, interval=60, first=0)
            │  (registered in app/main.py, before application.run_polling())
            │
            ▼
        app/nightshift/
            service.py       ← NightShiftService: mode transitions, queue ops,
            │                   notification routing, brief generation — the
            │                   ONLY module other sprints import from
            ├── scheduler.py     ← pure, timezone-aware schedule math (testable
            │                       with an injected "now", no wall-clock dependency)
            ├── classifier.py     ← job-type registry + classification lookup
            ├── policy.py          ← prepare→validate→save_draft→await_approval
            │                        state machine; prohibited-category enforcement
            ├── notifier.py         ← severity routing, fail-closed default
            ├── brief.py             ← morning-brief consolidation
            ├── models.py             ← RuntimeMode, NightShiftConfig, QueuedJob,
            │                            NotificationEvent, MorningBrief, JobType
            ├── repository.py          ← parameterized SQL only
            └── schema.py                ← additive CREATE TABLE IF NOT EXISTS
```

`app/nightshift/` depends only on `app.memory.database.MemoryDatabase` and
`app.security.SENSITIVE_CONTENT_PATTERN`. It does not depend on
`app.execution`, `app.providers`, `app.google_workspace`, `app.dissertation`,
or `app.router` — the job-type registry references those domains only as
*labels* (strings), never as imports, so this sprint cannot accidentally
gain the ability to call into them.

## Modules and file paths

**New files only:**

```text
app/nightshift/__init__.py
app/nightshift/service.py
app/nightshift/scheduler.py
app/nightshift/classifier.py
app/nightshift/policy.py
app/nightshift/notifier.py
app/nightshift/brief.py
app/nightshift/models.py
app/nightshift/repository.py
app/nightshift/schema.py
tests/test_nightshift_scheduler.py
tests/test_nightshift_classifier.py
tests/test_nightshift_policy.py
tests/test_nightshift_notifier.py
tests/test_nightshift_brief.py
tests/test_nightshift_repository.py
tests/test_nightshift_service.py
docs/night-shift.md
```

**Existing files, additive changes only:**

```text
app/main.py         # construct + initialize NightShiftService; register the
                     # job_queue.run_repeating tick before run_polling()
requirements.txt      # python-telegram-bot[job-queue] (extra, not a new package)
.env.example            # NOVA_NIGHTSHIFT_TIMEZONE (default documented, no secret)
```

`app/config.py` and `app/telegram_bot.py` are **not touched**.

## State-transition matrix

| From \ Trigger | schedule reaches start_time | schedule reaches end_time | manual `set_mode` | restart |
|---|---|---|---|---|
| `active` | → `night_shift` | — | → any mode | unchanged |
| `night_shift` | — | → `active` | → any mode | unchanged (resumes schedule awareness) |
| `quiet` | *(no effect — sticky)* | *(no effect — sticky)* | → any mode | unchanged (stays `quiet`) |
| `maintenance` | *(no effect — sticky)* | *(no effect — sticky)* | → any mode | unchanged (stays `maintenance`) |

Every transition, automatic or manual, writes one row to
`nightshift_mode_transitions` (`from_mode`, `to_mode`, `actor` —
`"system:scheduler"` or `"user:<id>"` — `reason`, `transitioned_at`). Current
mode is always the latest row; there is no separate "current state" table to
fall out of sync.

## Job classification matrix

| Category | Safety class(es) | Automatic during `night_shift`? | Example (illustrative label only — no import) |
|---|---|---|---|
| `approved_overnight` | `read_only`, `repeatable`, `reversible`, `draft_only` | Yes — runs through prepare→validate→save_draft→await_approval | `"execution_status_check"`, `"draft_summary_prepare"` |
| `deferred_until_morning` | requires judgment / borderline reversibility | No — queued, surfaced in morning brief as awaiting approval | `"dissertation_review_prepare"` (drafting only, not executing a review) |
| `critical_notify_only` | n/a — not work, a signal | No execution; routes to §5's critical path | `"essential_service_repeated_failure"` |
| `prohibited` | `destructive`, `external` | Never, in any mode | `"git_commit"`, `"git_push"`, `"email_send"`, `"whatsapp_send"`, `"calendar_mutation"`, `"file_delete"`, `"file_move"`, `"document_overwrite"`, `"secret_change"`, `"purchase"`, `"destructive_migration"` |
| *(absent from registry)* | n/a | Never — rejected as unknown, distinct audit reason from `prohibited` | anything not listed above |

## Notification severity matrix

| Trigger | Severity | Rationale |
|---|---|---|
| Security risk detected | `critical` | Explicit requirement |
| Probable data loss | `critical` | Explicit requirement |
| Database corruption | `critical` | Explicit requirement |
| Repeated failure of an essential service | `critical` | Explicit requirement |
| Approved-overnight job completed | `informational` | Routine, morning brief only |
| Deferred/queued item awaiting approval | `attention_required` | Prioritized in brief, not urgent |
| Test failures | `informational`/`attention_required`, never `critical` | Explicit exclusion |
| Provider rate limits | `informational`/`attention_required`, never `critical` | Explicit exclusion |
| Ordinary document-processing error | `informational`/`attention_required`, never `critical` | Explicit exclusion |
| Classification/routing failure (internal error) | `critical` (fail closed) | Unknown risk defaults to notify, not silence |

## Database tables

Additive only, same idempotent `CREATE TABLE IF NOT EXISTS` pattern as every
prior sprint:

```sql
CREATE TABLE IF NOT EXISTS nightshift_mode_transitions (
    id INTEGER PRIMARY KEY,
    from_mode TEXT, to_mode TEXT NOT NULL
        CHECK (to_mode IN ('active','night_shift','quiet','maintenance')),
    actor TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
    transitioned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nightshift_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),   -- singleton row
    start_time TEXT NOT NULL, end_time TEXT NOT NULL,
    morning_brief_time TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'Asia/Jakarta',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nightshift_queue (
    id INTEGER PRIMARY KEY,
    job_type TEXT NOT NULL, category TEXT NOT NULL
        CHECK (category IN ('approved_overnight','deferred_until_morning',
                             'critical_notify_only','prohibited')),
    dedup_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','approved','rejected','running',
                           'completed','failed','deferred')),
    payload_ref TEXT,          -- reference/hash only, never raw content
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(job_type, dedup_key)
);
CREATE TABLE IF NOT EXISTS nightshift_notifications (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('informational','attention_required','critical')),
    delivery_mode TEXT NOT NULL CHECK (delivery_mode IN ('morning_brief','immediate')),
    summary TEXT NOT NULL DEFAULT '',       -- sanitized, sensitive-content guarded
    related_job_id INTEGER REFERENCES nightshift_queue(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nightshift_morning_briefs (
    id INTEGER PRIMARY KEY,
    brief_date TEXT NOT NULL,
    mode_at_generation TEXT NOT NULL,
    completed_overnight TEXT NOT NULL DEFAULT '[]',      -- sanitized JSON array
    attention_required TEXT NOT NULL DEFAULT '[]',
    awaiting_approval TEXT NOT NULL DEFAULT '[]',
    failed_safely TEXT NOT NULL DEFAULT '[]',
    runtime_health_summary TEXT NOT NULL DEFAULT '',
    generated_at TEXT NOT NULL,
    UNIQUE(brief_date)
);
CREATE TABLE IF NOT EXISTS nightshift_audit_log (
    id INTEGER PRIMARY KEY,
    event TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
```

No existing table or column is altered. `UNIQUE(job_type, dedup_key)` on the
queue is the duplicate-prevention mechanism (§11's "duplicate queue
prevention" test target). `UNIQUE(brief_date)` prevents more than one
consolidated brief per calendar day, enforcing "one consolidated brief
rather than repeated notifications."

## Service interfaces

```python
# app/nightshift/service.py — the only module other sprints import from
class NightShiftService:
    def initialize(self) -> None: ...
    def tick(self, now: datetime) -> None:
        """Called by the job_queue every interval; checks schedule
        boundaries, generates the morning brief when due. Takes `now`
        explicitly so tests never depend on wall-clock time."""

    def get_status(self) -> NightShiftStatus: ...
    def set_mode(self, mode: str, actor: str, reason: str) -> None: ...
    def wake_now(self, actor: str) -> None: ...  # night_shift -> active, manual

    def enqueue(self, job_type: str, dedup_key: str, payload_ref: str | None) -> QueuedJob:
        """Raises UnregisteredJobTypeError if job_type is not in the registry
        (including the explicit 'prohibited' case, with a distinct reason)."""
    def list_queue(self, status: str | None = None) -> list[QueuedJob]: ...
    def approve_queued_job(self, job_id: int, actor: str) -> QueuedJob: ...
    def reject_queued_job(self, job_id: int, actor: str, reason: str) -> QueuedJob: ...

    def notify(self, event_type: str, severity: str, summary: str,
               related_job_id: int | None = None) -> NotificationEvent: ...
    def get_latest_morning_brief(self) -> MorningBrief | None: ...

    def register_job_executor(self, job_type: str, executor: JobExecutor) -> None:
        """Reserved for a future automation sprint. Raises if job_type is
        unregistered or prohibited."""
```

## Security constraints

- No dissertation body text, raw provider response, or raw error body is
  ever persisted — only sanitized summaries, hashes, and references
  (consistent with `app/dissertation/`'s and `app/providers/`'s own rules).
- `summary`/`reason`/`detail` free-text fields pass through
  `SENSITIVE_CONTENT_PATTERN` before storage, same as every other subsystem.
- Critical-notification logic fails closed (§5) — this is the one place in
  this sprint where "safe default" means "notify," not "stay silent."
- Manual mode overrides are always attributed (`actor`) and reasoned
  (`reason`) in `nightshift_mode_transitions` — fully auditable.
- No shell, subprocess, filesystem write outside the SQLite DB, network
  call, or Git action exists anywhere in this sprint's code — this is the
  structural guarantee that makes the "prohibited" category unbreakable:
  even if a `job_type` were somehow misclassified, there is no code path in
  `app/nightshift/` capable of performing a git/email/Calendar/file action
  in the first place. Enforcement of the prohibition happens twice
  (classification *and* absence of capability), not once.
- No test performs a real external action (no real Git, no real message
  send, no real Calendar/Drive call, no real Telegram send).

## Backward-compatibility requirements

- `NightShiftService` is constructed unconditionally (it has no external
  credentials to be missing, unlike the optional `ProviderGatewayService`)
  but its `tick()` is a no-op outside scheduled boundaries — a fresh install
  with default config starts in `active` mode and behaves exactly as today.
- No existing command, service, table, or test is affected.
- All existing tests (273 at last review, plus whatever Sprint 5A/5C/6A.0
  added since) remain green, unmodified.

## Tests

- **Mode transitions**: every matrix cell in the state-transition matrix,
  including the "sticky" `quiet`/`maintenance` non-transitions and restart
  persistence (construct a fresh `NightShiftService` against a DB seeded
  with a prior mode row; assert it resumes that mode, not `active`).
- **Schedule crossing midnight**: `start_time=22:00, end_time=06:00`
  correctly identifies 23:00 and 02:00 as "inside the window" and 12:00 as
  outside, using injected `now` values — no wall-clock dependency.
- **Timezone handling**: the same UTC instant is classified differently
  under `Asia/Jakarta` vs. another configured timezone; default is verified
  to be `Asia/Jakarta` when unset.
- **Restart persistence**: config and mode both survive a service
  reconstruction against the same DB.
- **Duplicate queue prevention**: enqueuing the same `(job_type, dedup_key)`
  twice raises/no-ops rather than creating a second row.
- **Unknown job rejection**: an unregistered `job_type` raises
  `UnregisteredJobTypeError` and is never persisted to the queue.
- **Safe job allowed**: an `approved_overnight` job reaches
  `await_approval` via prepare→validate→save_draft during `night_shift`.
- **Destructive/external job deferred**: a `deferred_until_morning` or
  `prohibited` job type never executes; `prohibited` is rejected with a
  distinct audit reason from "unregistered."
- **Notification severity routing**: each row of the severity matrix,
  including the fail-closed default when classification raises.
- **Morning brief consolidation**: multiple events in one night produce
  exactly one brief row (`UNIQUE(brief_date)` enforced); brief content
  contains no fixture "secret"/sensitive substring.
- **Critical-only immediate notification**: only `critical` severity ever
  gets `delivery_mode='immediate'`; all others are `morning_brief`.
- **No secret leakage**: fixture tokens/secrets never appear in any queue,
  notification, or brief row.
- **Migration idempotency**: `initialize()` called twice against a fresh DB
  and against a DB pre-seeded with rows from every other sprint's schema
  succeeds without altering unrelated tables.
- **Full regression compatibility**: entire existing suite passes unchanged.

## Acceptance criteria

- [ ] All four modes persist across a service restart; only `active`↔
      `night_shift` transition automatically, `quiet`/`maintenance` are sticky.
- [ ] Midnight-crossing and non-default-timezone schedules classified correctly.
- [ ] Job-type registry is a hard allowlist; unregistered and prohibited
      types are both rejected, with distinguishable audit reasons.
- [ ] No code path in `app/nightshift/` can perform a git, email/WhatsApp,
      Calendar, file, or purchase action — verified by the same
      subprocess/shell-blocking test pattern established in
      `tests/test_execution_security.py`.
- [ ] Critical notifications route to `immediate` delivery; everything else
      to the morning brief; classification failure defaults to critical.
- [ ] Exactly one morning brief per calendar day, containing the five
      required sections, no sensitive content.
- [ ] `app/telegram_bot.py` and `app/config.py` are untouched.
- [ ] `app/run_singleton.py`, `scripts/service.sh`, and `configure_logging()`
      are untouched.
- [ ] All existing tests plus all new Sprint 5A.1 tests pass.

## Operational limitations

- No real work executes yet — `approved_overnight` jobs reach
  `await_approval` and stop there; an actual executor is a future sprint's
  responsibility via `register_job_executor()`.
- The periodic tick relies on the bot process staying up all night (the
  existing Sprint 5A launchd `KeepAlive` is what makes this viable — this
  sprint does not add its own resilience beyond what 5A already provides).
- Health summary in the morning brief is limited to whatever
  `app/nightshift/` itself observes plus a read of the existing PID-liveness
  check; it is not a full application-level heartbeat (that gap, noted in
  Sprint 5A's own Accepted Limitations, remains open).
- Single-instance safety still depends on running via
  `app/run_singleton.py`; running `python -m app.main` directly still
  bypasses the lock, exactly as documented in Sprint 5A.

## Manual smoke-test procedure

1. Set `nightshift_config` via direct DB insert (or a temporary test script)
   to a window a few minutes from now in `Asia/Jakarta`.
2. Start NOVA via `python -m app.run_singleton` and confirm (via a debug log
   line or direct SQLite query) mode transitions `active → night_shift` at
   the configured start time.
3. Manually insert one `approved_overnight` and one `prohibited` job into
   the queue (direct DB insert, not a Telegram command since none exist
   yet); confirm only the approved one reaches `await_approval` and the
   prohibited one is rejected with a logged reason.
4. Confirm the scheduled `morning_brief_time` produces exactly one
   `nightshift_morning_briefs` row containing both a completed/awaiting
   item and no raw content.
5. Stop NOVA mid-`night_shift`, restart, and confirm mode is still
   `night_shift` (not reset to `active`).
6. Manually set mode to `quiet`, cross the scheduled boundary, and confirm
   the scheduler did not override it back to `active`/`night_shift`.

## Risks and technical debt

- **`job_queue` extra is a new transitive dependency (APScheduler)** —
  small, well-established library, but it's a real addition; flagged
  explicitly rather than silently bundled.
- **No real executor yet** means this sprint's practical user-visible value
  is zero until a future automation sprint plugs in via
  `register_job_executor()` — expected and acceptable for a "foundation"
  sprint, same pattern as 5C and 6A.0.
- **Health summary is shallow** until Sprint 5A's own heartbeat gap is
  closed; the morning brief's `runtime_health_summary` section will be
  correspondingly thin at first.
- **Timezone data correctness** depends on the Python runtime's `zoneinfo`
  database being present/current on the operator's Mac — worth a startup
  sanity check (`ZoneInfo("Asia/Jakarta")` resolves) rather than discovering
  a missing tzdata issue at 2am.
- **Sticky-mode interaction with a future `/wake` command**: `wake_now()` is
  specified as `night_shift → active` only — calling it while in `quiet` or
  `maintenance` should be a defined no-op/error, not an implicit mode
  change; this sprint's tests must cover that boundary even though the
  Telegram command itself is deferred to Sprint 5B.
