# Sprint 7E — Executive Dashboard Skeleton

## Status: **FROZEN** — final ChatGPT/Control Tower architecture decisions applied.

See `docs/WAVE_6_SHARED_CONTRACTS.md` §2.7, AD-W6-07 for the contract this
spec implements.

**Frozen dashboard policy (AD-W6-07, final):** localhost only, read only,
default OFF, explicit startup, no public bind, no new persistence, **no
automatic server startup with the NOVA bot**. `app/main.py` is not touched
by this sprint under any circumstance — see §3/§6/§7/§13.

## 1. Objective

Create a read-only executive visibility layer that consumes canonical
domain services directly — no second persistence model, no write path.

## 2. User-visible usable capability

Running a separate, explicitly-invoked command (e.g. `python -m
app.dashboard.server`) — never a side effect of running the bot — and
opening `http://127.0.0.1:<port>/` in a browser shows: projects, active
`WorkItem`s, pending approvals/decisions, agent assignments, Night Shift
state, provider health, and the Morning Brief — all read-only, all sourced
from existing services.

## 3. Scope

- New `app/dashboard/` package: `server.py` (standalone entry point,
  invoked via `python -m app.dashboard.server`; stdlib `http.server`/
  `wsgiref` wiring, `127.0.0.1`-only bind), `views.py` (route handlers,
  each one read-only composition against an injected service), `service.py`
  (`DashboardService`, the single class every view calls into — no view
  calls a domain service directly, keeping the read boundary in one place).
- `server.py`'s `main()` — the sprint's only entry point:
  1. Calls the existing `load_settings()` (`app/config.py`) and refuses to
     start (prints a message, exits non-zero) unless
     `NOVA_DASHBOARD_ENABLED` is true — a defense-in-depth guard, not the
     primary control (the primary control is that nothing else ever calls
     this function).
  2. Constructs its **own** read-only service instances
     (`ControlTowerService`, `NightShiftService`, `ProviderGatewayService`,
     and, once merged, `ExecutiveBriefService`/`AgentAssignmentService`/
     `KnowledgeService`) against the same `NOVA_MEMORY_DB_PATH` SQLite file
     the bot process uses — a second, fully independent process reading the
     same database file, never a thread sharing the bot's in-memory service
     objects. This reuses `MemoryDatabase`'s existing per-call-connection
     pattern (every domain service already opens/closes a connection per
     call rather than holding one open) — SQLite's default journal mode
     already supports one writer (the bot) and additional readers (the
     dashboard) without any schema or locking change.
  3. Starts the `http.server`/`wsgiref` listener on `127.0.0.1:<port>`.
- Populate the previously-empty `templates/` directory with server-rendered
  HTML templates (Python `string.Template` or stdlib `html`-escaped
  f-strings — no new templating dependency).
- New settings: `NOVA_DASHBOARD_ENABLED` (default `false`),
  `NOVA_DASHBOARD_PORT` (default e.g. `8787`), added to `app/config.py`
  additively (new optional fields, existing `Settings` fields unchanged) and
  `.env.example`. Neither field is read by `app/main.py` or
  `app/telegram_bot.py` — only by `app/dashboard/server.py`.

## 4. Out of scope

- Any write action from the dashboard (no approve/reject/capture button —
  Telegram remains the only write surface for Wave 6).
- Any new pip dependency (no Flask/FastAPI/Jinja2/React — AD-W6-07).
- External network exposure — `127.0.0.1` bind only, no reverse proxy, no
  TLS termination, no auth beyond "only reachable from the local machine."
- A second database, cache, or persistence layer of any kind.
- Real-time push/websockets — plain page reload is sufficient for a
  skeleton.

## 5. Existing architecture reused

- Every data source is an existing, already-canonical service: this
  sprint's entire job is composition, not new domain logic — same
  discipline as 7C's `ExecutiveBriefService`, one layer up (7E may itself
  call `ExecutiveBriefService` rather than recomposing brief data itself).
- `templates/` — the empty top-level directory reserved since Sprint 1.1
  ("Reusable structured output and workflow templates" per
  `docs/ARCHITECTURE.md`) is used for its originally documented purpose for
  the first time.
- `app/config.py`'s existing `Settings`/`load_settings()` additive-field
  pattern (every prior sprint that added configuration — Google Workspace,
  Night Shift, providers — added new optional fields the same way).
- `SECURITY.md`'s "least privilege" / "future integrations must request
  only the minimum scopes required" principle — applied here as "no network
  exposure beyond localhost, no new external dependency."

## 6. Owned files/modules

- `app/dashboard/{server,views,service}.py` — new, 7E-exclusive.
- `templates/*.html` — new, 7E-exclusive (first real use of this
  directory).
- `app/config.py` — additive new settings fields only.
- `.env.example` — additive new documented placeholder entries.
- `app/telegram_bot.py` — optional additive block only if a
  `/dashboardstatus` read-only command is added (shows whether the
  dashboard is *configured* as enabled and its URL — the command only
  reads the setting; it never starts the server) — not required for the
  skeleton.
- `tests/test_dashboard_*.py` — new, 7E-exclusive.
- **`app/main.py` is explicitly not in this list** — 7E does not edit it,
  per the AD-W6-07 freeze.

## 7. Shared dependencies

- `app/control_tower/**`, `app/nightshift/**`, `app/providers/**` — read
  only, existing public methods.
- `app/brief/**`, `app/agent_assignment/**`, `app/knowledge/**` — read only,
  once merged (all optional, `None`-safe).
- `app/config.py`, `.env.example` — shared, ordered append (new fields
  only). **`app/main.py` is not a dependency of this sprint at all** — the
  dashboard is a fully independent process/entry point.

## 8. Data/contracts

No new persisted contract. `DashboardService` exposes read-only view models
(plain dataclasses, dashboard-local, never written back to any domain):

```python
@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: str
    projects: list[Project]
    active_work_items: list[WorkItem]
    pending_approvals: list[Approval]
    pending_decisions: list[Decision]
    agent_assignments: list[AgentAssignment] | None    # None if 7D unmerged
    night_shift_mode: RuntimeModeState
    provider_health: dict[str, str] | None              # None if provider gateway disabled
    morning_brief: BriefItem | None                     # None if 7C unmerged
```

## 9. Security constraints

- Server binds `127.0.0.1` only — never `0.0.0.0`, never a configurable
  bind host in v1 (removing that option removes an entire class of
  accidental-exposure risk).
- No authentication is implemented in v1 because there is no network path
  to it beyond the local machine; this is a deliberate, documented trade-off
  (§14), not an oversight — a future sprint adding remote access must add
  authentication as part of that same change, not retrofit it later.
- All rendered values pass through `html.escape()` — no template accepts
  raw user-influenced content without escaping, even though all displayed
  content already passed `SENSITIVE_CONTENT_PATTERN` screening at write
  time in its owning domain.
- No environment variable, token, API key, or file path is ever rendered.
- Disabled by default (`NOVA_DASHBOARD_ENABLED=false`) — enforced inside
  `app/dashboard/server.py`'s own `main()` (§3). The bot's behavior is
  unaffected by this flag in either state, since `app/main.py` never reads
  it and never imports `app/dashboard/`.

## 10. Tests

- `DashboardService` composition — each field populated correctly from a
  fixture DB; graceful `None` handling for every optional dependency.
- Server binds only to `127.0.0.1` (asserted, not just assumed) and
  `server.py`'s `main()` refuses to start if `NOVA_DASHBOARD_ENABLED` is
  unset/false.
- HTML output is escaped — a test injects a value containing `<script>` and
  asserts it renders as text, not markup.
- **Regression/isolation test (the most important test in this sprint):**
  importing `app.main` and running its startup sequence (as every existing
  regression test already does) never imports `app.dashboard` and never
  binds any port — asserted directly, not just assumed, e.g. by grepping
  `app/main.py` for the absence of any `app.dashboard` import/reference, or
  by asserting no socket is listening after `app.main`'s service
  construction completes in a test harness.

## 11. Acceptance criteria

1. `app/main.py` is not modified by this sprint at all (`git diff` against
   it is empty), and running the bot (`python -m app.main`) never starts
   the dashboard under any settings combination — NOVA's behavior is
   byte-for-byte unchanged from pre-Wave-6 regardless of
   `NOVA_DASHBOARD_ENABLED`'s value.
2. Running `python -m app.dashboard.server` with `NOVA_DASHBOARD_ENABLED=true`
   makes `http://127.0.0.1:<port>/` render projects, active work items,
   pending approvals/decisions, Night Shift mode, and (when merged) agent
   assignments/morning brief/provider health — all read-only. With the flag
   false or unset, the same command exits without binding a port.
3. No write endpoint exists anywhere in `app/dashboard/`.
4. No new pip dependency is added to `requirements.txt`/`pyproject.toml`.
5. Full existing regression suite passes unmodified.

## 12. Integration contract

Wave 3 — last to merge, after 7A, 7B, 7C, 7D (§5 of the shared contract).
Its own tests can be written and largely run against fixtures/fakes before
the other four merge, but end-to-end acceptance requires all four landed.
Because 7E never touches `app/main.py`, its merge carries zero risk of
conflicting with any other sprint's `app/main.py` append block.

## 13. Explicit prohibited edits

- No edits to any other package's files — 7E is composition-only.
- No new pip dependency.
- **No edit to `app/main.py` under any circumstance** — this is the
  AD-W6-07 freeze, not merely a default. A future sprint that wants the
  dashboard to start with the bot must make that change explicitly, as its
  own scoped decision — it is not an option left open here.
- No change to the default (`NOVA_DASHBOARD_ENABLED=false`) behavior of
  `app/dashboard/server.py`'s own entry point.

## 14. Known risks / technical debt

- **Two independent processes reading the same SQLite file:** the dashboard
  (when explicitly started) and the bot each open their own connections
  against `NOVA_MEMORY_DB_PATH`. This is safe under SQLite's default
  journal mode (one writer, multiple readers) and requires no schema
  change, but means the dashboard's view can be momentarily stale relative
  to an in-flight bot transaction — acceptable for a read-only visibility
  skeleton, and no worse than the eventual consistency every existing
  per-call `MemoryDatabase.connection()` usage already exhibits across
  NOVA's own services.
- **No authentication in v1** (§9) — acceptable only because the bind is
  localhost-only; this constraint must be treated as load-bearing, not
  incidental, by any future sprint that touches `app/dashboard/`.
- **`templates/` was empty since Sprint 1.1** — this sprint is the first to
  give it real content; there is no existing convention to follow for
  template file naming/structure inside NOVA, so 7E's implementer sets that
  convention for future sprints (e.g. a future Sprint 7B follow-up could
  reuse it for knowledge-item detail pages).
