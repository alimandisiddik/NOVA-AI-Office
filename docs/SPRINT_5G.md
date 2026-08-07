# Sprint 5G — Multi-Provider Fallback Hardening

## Status: SPECIFICATION ONLY — not implemented, not merged

This document is an architecture and specification freeze. It contains no
production code changes. It was produced by inspecting the real repository
state as of this writing (baseline: `main`, `556 passed` on `pytest -q`,
working tree clean).

---

## 1. Existing Provider/Router Assessment

Inspected directly (source, not assumption):

- **`app/router/`** — `classifier.py` (deterministic keyword→workflow
  classifier), `workflows.py` (workflow→role registry), `roles.py`
  (role→provider *label* registry — labels only, e.g.
  `TECHNICAL_ARCHITECT → "Claude"`, `EXECUTION_WORKER → "Codex"`,
  `WORKSPACE_KNOWLEDGE → "Gemini"`; every role's `connection_status` is
  `NOT_CONNECTED` — these are planning labels, not live adapters), `risk.py`
  (risk level + approval mode: `GOOGLE_WORKSPACE` is already hard-classified
  `HIGH`/`REQUIRED`), `planner.py` (`generate_plan()` composes all four into
  an `ExecutionPlan`). This is a pure, provider-agnostic classifier — no
  network call, no provider SDK. **This is the "Task Classifier" the
  objective diagram calls for; it already exists and is reused, not
  rebuilt.**
- **`app/providers/`** — `registry.py` (static `MODEL_REGISTRY` of 9Router
  aliases: `nova-v1`, `nova-v1-fallback`, `nova-v1-fast`, plus disabled/
  isolated entries — every entry's `provider_id` is `"9Router"`; there is
  currently exactly **one** provider), `selection.py`
  (`select_eligible_models()` — deterministic, priority-order-preserving,
  workflow/role-scoped, fallback-group-pinned eligibility filter),
  `service.py` (`ProviderGatewayService` — the real execution engine: SHA-256
  prompt hashing, sensitive-content pre-filter, HIGH-risk pre-filter,
  in-memory per-model circuit breaker, `MAX_TOTAL_ATTEMPTS = 3` already
  enforced, full attempt-level + request-level audit write), `ninerouter.py`
  (`NineRouterAdapter` — OpenAI-compatible HTTP adapter, maps HTTP status
  codes to typed `ProviderError` subclasses, rejects redirects, no shell/
  subprocess), `errors.py` (typed exception hierarchy with `.category`),
  `adapter.py` (the `ProviderAdapter` Protocol — one async method,
  provider-agnostic), `models.py`, `repository.py`, `schema.py` (SQLite audit
  store: `provider_request_audit` + `provider_request_attempts`, additive
  migrations already in the codebase's established style).
- **`app/dispatch/`** — `DispatchService`/`ApprovalService`/`AgentRegistry`:
  a generic, already-hardened state machine (`pending → dispatching →
  running → succeeded/failed/timed_out`, CAS-protected, idempotency-keyed,
  approval-gated by capability class) for *agent capability* execution. Only
  one adapter exists (`local_deterministic` — simulated, zero side effects).
  This is **not** provider-model dispatch; it is the seam Night Shift already
  uses to reach agents.
- **`app/control_tower/service.py`** — `ControlTowerService` owns work
  items, decisions, approvals aggregation (across control-tower, execution,
  night-shift, and dispatch sources already). **It does not call
  `ProviderGatewayService` anywhere.** There is no code path today from
  Control Tower to a live model call.
- **`app/nightshift/worker.py`** — `NightShiftWorker` claims/executes jobs
  strictly through `DispatchService`. It has never called
  `ProviderGatewayService`. Every "executed" night job today resolves to the
  single `local_deterministic` adapter — i.e., **Night Shift has never made
  a real model call**, let alone a 9Router-first continuity call.
- **Telegram surface** — `/ask` (`app/telegram_bot.py:619`) calls
  `ProviderGatewayService.generate_text()` **directly**, bypassing Control
  Tower entirely. This is the only live caller of the provider gateway in
  the running bot.
- **Direct specialist adapters** — do not exist. `app/execution/adapter.py`
  explicitly documents this as a constraint: *"no provider = True — no
  OpenAI, Gemini, Claude, Codex SDK"*. `app/dispatch/adapters.py` registers
  only `local_deterministic`. Confirmed by repo-wide search: zero references
  to Codex/Claude/Gemini/Anthropic/OpenAI SDKs outside of the `roles.py`
  planning labels and a code comment.
- **`app/google_workspace/`** — real OAuth, real Calendar/Drive service
  calls, least-privilege scopes. This is a genuinely different kind of
  "provider" (a connector with credentials and side effects), not a
  text-generation backend.
- **Config** (`app/config.py`, `.env.example`) — one global provider config:
  `NOVA_PROVIDER_BASE_URL`, `NOVA_PROVIDER_API_KEY`,
  `NOVA_PROVIDER_MODEL_PRIORITY` (≤ 3 entries, enforced), no per-role or
  per-workflow provider configuration exists.

**Conclusion: the bounded, auditable, circuit-broken fallback machinery this
sprint asks for already exists — for a single provider.** The work is
extension (multi-provider chains, typed outcome taxonomy, a Control-Tower
seam, Night-Shift continuity wiring), not invention.

---

## 2. Gap Analysis

| # | Gap | Evidence | Severity |
|---|-----|----------|----------|
| G1 | Control Tower never calls the provider gateway. `/ask` bypasses Control Tower entirely. | `grep` of `control_tower/service.py` — no provider import. `telegram_bot.py:631` calls `provider.generate_text()` directly. | High — blocks AD-2 |
| G2 | No direct specialist adapters exist at all (Codex/Claude/Gemini). | `execution/adapter.py` docstring; `dispatch/adapters.py` only `local_deterministic`. | High — blocks primary-specialist routing (B, C) |
| G3 | Provider selection is single-provider; `select_eligible_models` and `fallback_group` operate only within 9Router's own alias list — there is no concept of a cross-provider chain (specialist → combo). | `providers/selection.py`, `providers/service.py:154-160`. | High — core of this sprint |
| G4 | Audit trail cannot distinguish which *provider* made an attempt, only which *model_id*. `provider_id` is hardcoded to `"9Router"` in `service.py:192` and `service.py:322`, and `ProviderRequestAttempt` has no `provider_id` field. | `providers/models.py:30-38`, `providers/service.py:301-343`. | Medium — blocks AD-8 |
| G5 | Night Shift has never made a real provider call; nothing wires `NightShiftWorker` to `ProviderGatewayService`. | `nightshift/worker.py` imports only `app/dispatch/*`. | High — blocks E (continuity test) |
| G6 | A circuit-open skip still consumes one of the 3 attempt slots in the outer `enumerate()` loop, and is not recorded in the audit `attempts` list at all (silent gap between "3 configured aliases" and "3 audited attempts"). | `providers/service.py:176-186`. | Low/Medium — correctness nit, not a blocker |
| G7 | `NOVA_PROVIDER_MODEL_PRIORITY` is one global list shared by every workflow/role — there is no per-role "Coding Combo" vs "Review Combo" distinction, even though `RegisteredModel.supported_workflows`/`supported_roles` already has the *capability* to scope per-workflow (this is a data gap, not a mechanism gap). | `providers/registry.py`, `config.py:77-80`. | Low — cheap to close |
| G8 | Provider migrations use a blind `try/except sqlite3.OperationalError: pass` (`providers/repository.py:33-36`), whereas the newer, stricter convention established in Sprint 5F uses a `PRAGMA table_info(...)` existence guard before every `ALTER TABLE`. | `providers/repository.py` vs `docs/SPRINT_5F.md` §3. | Low — hardening, not a blocker |
| G9 | `.env.example` and `Settings` have no slot for specialist adapter configuration (executable path, API key, enabled flag) per role. | `app/config.py`. | Medium — needed for AD-3 |

None of these gaps require touching `app/dispatch/`'s public interface,
`app/nightshift/service.py`'s `JOB_TRANSITIONS`, or
`app/control_tower/repository.py`'s schema — every gap closes additively.

---

## 3. Architecture Decisions (AD-1 … AD-14)

### AD-1 — Where does provider fallback policy live?
Inside `app/providers/`, alongside the existing `registry.py`/`selection.py`/
`service.py`. A new `app/providers/outcomes.py` (typed failure taxonomy +
fallback-eligibility set) and an extension of `selection.py` (chain
resolution now spans `provider_id`, not just `model_id`) are the policy
layer. `ProviderGatewayService` remains the single orchestrating entry
point — it does not move into `app/dispatch/` or `app/control_tower/`.
This is a strict reading of §10: **provider selection, eligibility,
fallback ordering, and failure classification stay inside the provider
package.**

### AD-2 — How does Control Tower request provider execution?
Through the **existing `DispatchService` seam**, not a new bespoke path.
Control Tower (or anything acting on its behalf) issues a normal
`DispatchRequest` against a new agent entry (e.g. `coding_agent`,
`architecture_agent`, `generic_ai_agent`) whose `adapter_id` resolves to a
new `AgentAdapter` implementation that internally calls
`ProviderGatewayService`. `DispatchService`'s existing state machine,
idempotency, CAS transitions, and approval gating apply unchanged — this
mirrors exactly how `NightShiftWorker` already reaches agents, and it means
Control Tower gains provider access with **zero new orchestration code**.
Dispatch-level attempt tracking (`DispatchRecord.attempt_count`,
1 attempt = 1 job, exactly as Night Shift already sets `max_attempts=1` and
manages its own retry timing outside Dispatch) stays a separate concern from
the provider layer's own internal bounded 3-attempt fallback, which happens
entirely inside one `adapter.execute()` call. Two audit granularities,
already the established pattern (Dispatch audit vs. Night Shift audit
coexist today the same way).

`/ask` is explicitly exempted from this seam: it is a low-risk, synchronous,
interactive read-only path (`GENERAL`/`FAST` workflow, `NONE` approval) and
continues to call `ProviderGatewayService.generate_text()` directly, as it
does today — routing it through Dispatch would add latency and approval
machinery to a command that is already correctly unapproved.

### AD-3 — How are direct specialist providers represented?
Minimal, protocol-only. Reuse the existing `ProviderAdapter` Protocol
(`app/providers/adapter.py` — already provider-agnostic, one async method).
Two new adapters, **not three massive implementations**:
- `CodexAdapter` and `ClaudeAdapter` — thin, config-gated. If not configured
  (no executable path / no session marker present), the adapter is simply
  absent from the candidate chain — never attempted, never a phantom
  failure.
- Gemini is **not** a generic text adapter in this sprint — see AD-12; the
  Google Workspace primary path stays the real `app/google_workspace/`
  services, not a `ProviderAdapter`.

Per §8, subscription/session-backed CLIs (ChatGPT Plus / Claude Pro sessions)
are explicitly **not** treated as generic API credentials. The adapter
contract is deliberately narrow:
```
dispatch  -> generate_text(request, timeout_seconds) -> ProviderResponse
status    -> not required (synchronous call, bounded timeout)
cancel    -> not required for v1 (synchronous call; cooperative cancellation
             is future scope)
availability -> is_available() -> bool   (new, cheap, side-effect-free)
```
Initial ship: both adapters are safe stubs that report `provider_unavailable`
until a real executable/session is configured. This lets the full fallback
chain (specialist → combo) be tested end-to-end from day one without betting
the sprint on CLI integration risk — a deliberate, documented scope cut, not
an oversight.

### AD-4 — How is 9Router represented?
Unchanged mechanism, richer data. `NineRouterAdapter` and `MODEL_REGISTRY`
stay exactly as implemented. "Combo" is realized the way the registry
*already supports but the config doesn't yet use*: multiple `nova-v1*`
aliases scoped to a workflow/role via `supported_workflows`/
`supported_roles` (already-existing fields), with priority order supplied
per combo group instead of one global list (closes G7). No new mechanism —
this is a data/config extension of `registry.py` + `config.py`.

### AD-5 — Exact fallback-eligible failure classes?
Adopted as specified, with the existing exception hierarchy mapped onto the
new typed taxonomy (`app/providers/outcomes.py`):

| Typed outcome | Existing exception(s) | Fallback-eligible? | Current behavior (before this sprint) |
|---|---|---|---|
| `success` | — | n/a (terminal) | — |
| `quota_exhausted` | `RateLimitError` (HTTP 429, quota-shaped body) | **Yes** | merged into `rate_limited` today |
| `rate_limited` | `RateLimitError` (HTTP 429) | **Yes** | already fallback-eligible |
| `timeout` | `TimeoutError` | **Yes** | already fallback-eligible |
| `provider_unavailable` | `ConnectionError` (network/5xx-safe), `CircuitOpenError`, adapter `is_available()==False` | **Yes** | `ConnectionError` already eligible; circuit/availability skips are new |
| `authentication_failed` | `AuthenticationError` (401) | **No — stop** | already stops today |
| `invalid_request` | `UnsupportedOperationError` (400/404/422) | **No — stop** | already stops today |
| `model_error` | `InvalidResponseError`, `OutputLimitError` | **No — stop (default)** | already stops today |
| `cancelled` | new: explicit caller cancellation | **No — stop** | not previously modeled |

**Critical review of the recommended policy:** accepted as-is, with two
refinements grounded in the existing code, not the prompt's suggestion
alone:
- `AuthorizationError` (403) is added to the non-fallback set alongside
  `authentication_failed` — it already behaves this way today (not in the
  fallback `except` tuple) and conflating "wrong credentials" with "no
  credentials" under one non-fallback bucket is simpler and already how the
  code behaves; no change needed, just documented.
- `quota_exhausted` and `rate_limited` are kept as **one exception type**
  (`RateLimitError`) at the adapter level, differentiated only at the typed
  audit-outcome level from response-body hints when available. Both are
  fallback-eligible, so the split has no gating effect — it exists purely
  for operator-facing telemetry ("we got rate-limited" vs "we're out of
  quota for the billing period" read very differently in a morning brief).
  Splitting the *exception class* itself would be over-engineering for a
  distinction with no behavioral difference.

### AD-6 — How are maximum attempts enforced?
`MAX_TOTAL_ATTEMPTS = 3` (existing module constant in `providers/service.py`)
stays the ceiling, now applied to the **cross-provider candidate list**
(e.g., `[codex_direct, ninerouter_combo_a, ninerouter_combo_b]`), not just a
single provider's alias list. One attempt = one live outbound call to one
adapter. A locally-known-unavailable candidate (circuit open, or
`is_available()==False`) is **skipped without consuming a live-attempt
slot** — this fixes G6: skips are recorded in the audit trail with a
distinct `status="skipped"` row, but do not count toward `attempt_count` or
the 3-slot ceiling. The loop still stops immediately on `success` or any
non-fallback-eligible outcome — attempts are never exhausted "for
completeness." This is a direct, minimal fix to the existing loop in
`providers/service.py`, not a rewrite.

### AD-7 — How is availability detected?
Two tiers, both synchronous and side-effect-free — no polling, no
background health-check daemon:
1. **Static/config availability** — is a direct adapter configured at all
   for this role (executable path or API key present)? If not, it is never
   placed in the candidate chain. This is how Night Shift structurally
   never depends on a local CLI: for Night/Background-classified roles, the
   chain is 9Router-only by *configuration*, not by a runtime check.
2. **Runtime availability** — for roles where a specialist *is* primary
   (Coding, Architecture), a cheap `is_available()` preflight (executable
   present on `PATH` + a bounded, argv-only, no-shell probe, or an API-key
   presence check) runs before the candidate is counted as a live attempt.
   Failure here yields `provider_unavailable` immediately and does not
   consume an attempt slot (per AD-6).

### AD-8 — How is provider attempt audit stored?
Additive migration to the existing `app/providers/schema.py` tables,
following the repo's established additive-column pattern:
- `provider_request_attempts` gains `provider_id` (closes G4) and a
  `status` value of `"skipped"` (closes G6).
- `provider_request_audit` gains `initial_provider_id`, `final_provider_id`
  (mirroring the existing `initial_model_id`/`final_model_id` pair), and
  `resolved_model_label` — the backing model label a provider's own
  response *reports* it used (9Router already returns this in
  `data.get("model", ...)`, captured today in `ProviderResponse.model_id`
  but not persisted separately from the requested alias). This directly
  supports AD-13's alias/identity separation without NOVA ever hardcoding
  an alias→identity mapping.
- Recommended hardening (not a blocker): migrate `providers/repository.py`'s
  blind `try/except sqlite3.OperationalError: pass` to the
  `PRAGMA table_info(...)`-guarded pattern Sprint 5F established for
  `night_queue_jobs`, for consistency (closes G8).

No secrets, no raw prompt content, no full provider responses are stored —
unchanged from today (`prompt_hash` only, sanitized `error_category` only).

### AD-9 — How does Night Shift use 9Router-first continuity?
Purely additive to `_JOB_TYPE_ROUTES`/`AGENT_REGISTRY`/`REGISTERED_JOBS` —
**zero changes to `worker.py`'s claim/execute/defer/recover/cancel logic**,
which already treats a dispatch outcome generically
(`succeeded`/`failed`/`timed_out`/`cancelled`). A Night/Background job type
that needs model inference gets a new route entry pointing at an agent
whose provider chain (per AD-4/AD-7) is 9Router-combo-only by
configuration — the specialist adapters are structurally absent from that
chain, so Night Shift cannot depend on local CLI/session availability even
by accident. This satisfies operating principle #3 structurally, not just
by convention, and respects Sprint 5F's stated constraint of no changes to
`app/dispatch/`'s public interface or `JOB_TRANSITIONS`.

### AD-10 — How does Coding use Codex-first specialization?
A `coding_agent` (or extended `development_agent`) entry routes to a
provider chain `[Codex direct, 9Router Coding Combo]` for the
`TECHNICAL`/`EXECUTION_WORKER` role. The chain itself lives entirely in
provider-policy configuration/registry data — **no coding handler contains
provider branching logic**, satisfying the explicit "do not hard-code
provider logic inside coding handlers" constraint.

### AD-11 — How does Architecture use Claude-first specialization?
Symmetric to AD-10: `[Claude direct, 9Router Review Combo]` for
`TECHNICAL`/`TECHNICAL_ARCHITECT`. Independent auditability is automatic —
every attempt (specialist or combo) lands in the same
`provider_request_audit`/`provider_request_attempts` tables keyed by
`request_id`/`execution_id`, giving a complete per-review trail without a
second audit system.

### AD-12 — How does Google Workspace preserve real Google-operation boundaries?
The provider-fallback chain applies **only to text/reasoning generation**,
never to Google API mutations. `app/google_workspace/{calendar,drive}`
stays the untouched, real-credentialed path for anything that reads/writes
Drive, Calendar, or Gmail — that is not a `ProviderAdapter` and must never
enter the generic fallback chain. Only the reasoning/summarization portion
of a Workspace task may fall back to 9Router (e.g., "summarize these
events"); anything requiring an actual Workspace mutation has no 9Router
equivalent and fails closed (`provider_unavailable`, no silent
substitution) rather than pretending a mutation occurred. This sits
downstream of the existing, unchanged risk gate: `GOOGLE_WORKSPACE` is
already `HIGH`/`REQUIRED` in `app/router/risk.py`, so `ApprovalService`
already gates it before any dispatch happens — the provider layer adds no
new bypass.

### AD-13 — How are 9Router aliases/config separated from actual model identities?
Formalizes what `registry.py` already implicitly does. `RegisteredModel.
model_id` (e.g. `nova-v1-coding-a`) is a **NOVA-internal route alias**,
opaque outside the 9Router platform's own configuration. Production code
must never hardcode an assumption of which frontier model an alias
resolves to. The only properties NOVA code may depend on are combo
membership, priority order, and workflow/role scope — all local registry
data. Where transparency is useful (telemetry, `/providerstatus`), NOVA
records what the *response itself* reports (`resolved_model_label`, AD-8),
never a static alias→identity table in source.

### AD-14 — What existing provider/router components remain authoritative?
- `app/router/{classifier,workflows,roles,risk,planner}.py` — **the**
  workflow/role/risk classifier. Untouched.
- `app/providers/registry.py` / `selection.py` — **the** model-eligibility
  engine. Extended (provider-scoped), not replaced.
- `app/providers/service.py` (`ProviderGatewayService`) — **the** single
  provider execution entry point. Extended, not duplicated.
- `app/dispatch/service.py` / `approvals.py` / `registry.py` — **the**
  single dispatch/approval state machine. Reused as the Control-Tower and
  Night-Shift seam.
- `app/control_tower/service.py` — **the** orchestration authority. Gains
  one additive call path into Dispatch; no orchestration logic duplicated.
- `app/nightshift/worker.py` — lifecycle logic untouched; only registry
  data gains entries.

---

## 4. Proposed Provider-Selection Architecture

```
Control Tower / Night Shift Worker / /ask
        │
        ▼
DispatchService.create_dispatch()+dispatch()   (Control Tower / Night Shift path)
   or
ProviderGatewayService.generate_text()         (/ask direct path — unchanged)
        │
        ▼
ProviderGatewayService  (app/providers/service.py, extended)
   1. sensitive-content pre-filter        (unchanged)
   2. HIGH-risk pre-filter                (unchanged)
   3. resolve provider chain for (workflow_id, role_id)
        — chain = ordered [ (provider_id, model_id, adapter_ref), ... ]
        — sourced from extended registry.py (specialist entries + combo groups)
   4. for each candidate, up to MAX_TOTAL_ATTEMPTS=3 LIVE attempts:
        a. availability check (AD-7) — skip, no slot consumed, audited as "skipped"
        b. circuit-breaker check (existing, per provider+model)         — skip, no slot consumed
        c. adapter.generate_text(request, timeout)
        d. map exception -> typed outcome (AD-5)
        e. fallback-eligible?  -> continue to next candidate
           non-fallback-eligible? -> stop, surface typed failure
        f. success -> stop, return response
   5. write full audit trail (AD-8)
```

`select_eligible_models` (existing) becomes the per-provider-group
sub-filter; a new thin wrapper composes candidates across provider groups in
configured order before handing the flattened, capped list to the same
attempt loop shape that exists today.

---

## 5. Failure Taxonomy

See AD-5 table. Nine typed outcomes total (`success` + 8 failure classes),
each mapped from the existing `ProviderError` category hierarchy — no new
exception types are required at the adapter boundary; the typed outcome is
a classification layer computed from the existing `.category` attribute
plus new local conditions (skip, cancelled).

---

## 6. Fallback Policy Matrix

| Role (workflow) | Primary | Fallback | Max attempts | Notes |
|---|---|---|---|---|
| Night/Background/Autonomous | 9Router Combo (general) | — (no further fallback; combo *is* the primary) | 3 (within combo group) | Never includes a specialist candidate — structural, not conditional |
| Coding (`TECHNICAL`/`EXECUTION_WORKER`) | Codex direct | 9Router Coding Combo | 3 total | Codex absent from chain entirely if unconfigured |
| Architecture/Review (`TECHNICAL`/`TECHNICAL_ARCHITECT`) | Claude direct | 9Router Review Combo | 3 total | Same pattern as Coding |
| Google Workspace (`GOOGLE_WORKSPACE`) | Gemini / real `google_workspace` path | 9Router — reasoning-only subset, never for mutations | 3 total (reasoning path only) | HIGH risk / REQUIRED approval unchanged; mutation path has no fallback, ever |
| Generic AI (`GENERAL`/`FAST`/others) | 9Router Combo | server-side model fallback where 9Router supports it | 3 total | `/ask` path |

---

## 7. Audit Model

Extends the existing two-table model (`provider_request_audit` +
`provider_request_attempts`) additively — see AD-8. Every attempt (live or
skipped) is one row in `provider_request_attempts`, carrying:
`request_id`, `execution_id` (nullable, links to Dispatch/Execution where
applicable), `provider_id` (new), `model_id`/alias, `attempt_number`,
`created_at`, `status` (`success`/`failed`/`skipped`), `error_category`,
`latency_ms`. The request-level row adds `initial_provider_id`,
`final_provider_id`, `resolved_model_label`, alongside the existing
`fallback_used`/`fallback_reason`/`attempt_count`. No secrets, no raw
prompt, no full response — unchanged posture.

---

## 8. Adapter/Interface Contract

`app/providers/adapter.py`'s `ProviderAdapter` Protocol is extended (not
replaced) with one new required method:

```python
class ProviderAdapter(Protocol):
    async def generate_text(self, request: ProviderRequest, *, timeout_seconds: float) -> ProviderResponse: ...
    def is_available(self) -> bool: ...   # new — cheap, synchronous, no side effects
```

`NineRouterAdapter` gets a trivial `is_available()` (config presence — it is
always "available" if configured, since HTTP failures are already handled
per-attempt). `CodexAdapter`/`ClaudeAdapter` implement both methods with the
fixed-executable, argv-only, no-shell posture already documented as the
house standard in `execution/adapter.py`'s docstring. `status`/`cancel` are
explicitly **not** part of the v1 contract — every call is synchronous and
timeout-bounded, matching the existing `NineRouterAdapter` shape; adding
async status/cancel is out of scope until a genuinely long-running
specialist integration needs it.

---

## 9. File-Level Implementation Plan (for the future implementation sprint — not built now)

**New files**
- `app/providers/outcomes.py` — typed outcome enum + `FALLBACK_ELIGIBLE`
  frozenset + exception→outcome mapping.
- `app/providers/availability.py` — `is_available()` helper contracts.
- `app/providers/specialists/codex_adapter.py`, `claude_adapter.py` — stub
  adapters (provider_unavailable until configured).
- `app/dispatch/adapters.py` — add `ProviderGatewayAgentAdapter` (new class
  in the existing file, alongside `LocalDeterministicAgentAdapter`).

**Additive edits**
- `app/providers/models.py` — add `provider_id` to `ProviderRequestAttempt`;
  add `initial_provider_id`/`final_provider_id`/`resolved_model_label` to
  `ProviderAuditRecord`.
- `app/providers/schema.py` — additive `ALTER TABLE` migrations for the
  above (PRAGMA-guarded, per G8 hardening).
- `app/providers/registry.py` — specialist entries + combo-group scoping
  data.
- `app/providers/selection.py` — cross-provider chain resolution wrapper
  around the existing per-group filter.
- `app/providers/service.py` — generalize the attempt loop to chain
  candidates; add availability/skip handling (AD-6); typed outcome mapping.
- `app/dispatch/registry.py` — new agent entries (`coding_agent`,
  `architecture_agent`, `generic_ai_agent`, `night_shift_ai_agent`, or
  extend existing `development_agent`/`night_shift_agent`).
- `app/nightshift/worker.py` — `_JOB_TYPE_ROUTES` additions only, if any new
  job types require model inference (no logic changes).
- `app/config.py`, `.env.example` — additive specialist config
  (`NOVA_CODEX_EXECUTABLE_PATH`, `NOVA_CLAUDE_EXECUTABLE_PATH`, per-combo
  priority lists e.g. `NOVA_PROVIDER_CODING_COMBO_PRIORITY`).
- `app/main.py` — construct new adapters (if configured) and register new
  dispatch agent adapters; unchanged construction order otherwise.
- `app/telegram_bot.py` — extend `/providerstatus` output only (chain
  visibility); `/ask` unchanged.
- `docs/executive-control-tower.md`, `docs/agent-dispatch-and-approvals.md`
  — additive sections documenting the new seam.

**Untouched** (explicitly, per §9/§10/§11 boundaries): `app/control_tower/
repository.py` schema, `app/nightshift/service.py` `JOB_TRANSITIONS`,
`app/dispatch/schema.py`, `app/dispatch/service.py` public interface,
`app/google_workspace/*`.

---

## 10. Migration Plan

Purely additive SQLite `ALTER TABLE` columns on `provider_request_audit`
and `provider_request_attempts`, nullable/defaulted, following the same
pattern already used by every prior sprint's schema evolution in this
repo (`providers/schema.py`'s own `MIGRATIONS` tuple, `night_queue_jobs`'
five-column additive migration in 5F). No destructive change, no backfill
required, no breaking change to any existing query. Recommended
(non-blocking) hardening: switch the provider migrations from blind
`try/except sqlite3.OperationalError` to the `PRAGMA table_info(...)`
existence guard, matching 5F's stricter convention.

---

## 11. Test Plan

| Scenario | Target file | Notes |
|---|---|---|
| A. Coding primary success | `tests/test_provider_policy.py` (new) | Codex adapter stub returns success → no fallback attempted |
| B. Coding quota exhaustion | `tests/test_provider_policy.py` | Codex → `quota_exhausted` → 9Router Coding Combo succeeds |
| C. Architecture primary success | `tests/test_provider_policy.py` | Claude succeeds → no fallback |
| D. Architecture unavailable | `tests/test_provider_policy.py` | Claude `provider_unavailable` (unconfigured/preflight fail) → 9Router Review Combo succeeds |
| E. Night Shift continuity | `tests/test_nightshift_worker.py` (additive) | Specialist absent from chain by construction; job succeeds via 9Router with zero `worker.py` changes exercised |
| F. Rate limit | `tests/test_provider_policy.py` | Primary `rate_limited` → eligible fallback |
| G. Authentication failure | `tests/test_provider_policy.py` | `authentication_failed` → stop immediately, no hopping |
| H. Invalid request | `tests/test_provider_policy.py` | `invalid_request` → stop, no fallback |
| I. Maximum attempts | `tests/test_provider_policy.py` | Never exceeds 3 live attempts; skips don't count (AD-6) |
| J. Audit | `tests/test_provider_gateway.py` (extended) | `provider_id`, `initial_provider_id`/`final_provider_id`, `resolved_model_label`, `skipped` rows all round-trip correctly |
| K. All providers fail | `tests/test_provider_policy.py` | Deterministic final failure status, fully audited |
| L. Existing regression | full suite | `pytest -q` must stay ≥ 556 passed; Sprint 6A and all earlier capabilities intact |

New integration coverage: `tests/test_dispatch_provider_adapter.py` (new) —
Control-Tower-seam path: `DispatchService.dispatch()` → new agent adapter →
`ProviderGatewayService` → typed `DispatchResult`, using
`httpx.MockTransport` exactly as `test_provider_gateway.py` already does
(no live network calls in tests, matching existing convention).

---

## 12. Security Review

| Requirement (§12) | Status under this design |
|---|---|
| No secret logging | Unchanged: `prompt_hash` only, sanitized `error_category` only; new fields (`provider_id`, `resolved_model_label`) are non-secret identifiers |
| No API keys in DB | Unchanged: `/providerstatus` already masks `base_url` hostname and never surfaces `api_key`; specialist adapter config (executable path) is not secret-shaped |
| No shell command from untrusted text | Specialist adapters use fixed argv arrays built only from a fixed executable path + config flags + prompt passed via stdin/file — never string-interpolated into a shell, mirroring `execution/adapter.py`'s documented constraints |
| Local CLI adapters use fixed executable/protocol boundaries | Yes — `is_available()` and `generate_text()` both operate against a single configured executable path, never a user-supplied path |
| No arbitrary subprocess execution | Yes — argv-only, no `shell=True`, no dynamic executable resolution |
| Destructive actions remain approval-gated | Unchanged — `GOOGLE_WORKSPACE` HIGH/REQUIRED gate is untouched; provider layer never mutates external state |
| Provider auth failures must not leak credentials | Unchanged — `AuthenticationError` messages are fixed strings today; new specialist adapters must follow the same fixed-message convention |
| `.env` stays uncommitted | Verified: `.gitignore` already excludes `.env`/`.env.*`; new specialist env vars follow the same file |

---

## 13. Acceptance Criteria

- [ ] All 14 architecture decisions (AD-1…AD-14) implemented exactly as
      specified, with no ADR-deviating shortcuts.
- [ ] `MAX_TOTAL_ATTEMPTS = 3` enforced across cross-provider chains;
      skips never consume a live-attempt slot.
- [ ] Typed outcome taxonomy fully implemented; fallback-eligible set is
      exactly `{quota_exhausted, rate_limited, timeout,
      provider_unavailable}`.
- [ ] `authentication_failed`, `invalid_request`, `cancelled` never trigger
      fallback — verified by tests G and H.
- [ ] Night Shift reaches a successful outcome with zero specialist
      adapters configured — verified by test E — and `worker.py`'s
      diff is registry-data-only.
- [ ] No coding/architecture handler contains provider branching logic —
      structurally verified (chain composition lives only in
      `app/providers/registry.py`/config).
- [ ] `app/dispatch/`'s public interface, `JOB_TRANSITIONS`, and
      `app/control_tower/repository.py` schema are unmodified (`git diff`
      empty against those paths, matching the 5F precedent of proving this
      structurally).
- [ ] Google Workspace mutation path has zero fallback substitution —
      verified by a dedicated test asserting a mutation-shaped request
      never reaches the 9Router adapter.
- [ ] Full regression: `pytest -q` ≥ 556 passed, all prior tests green,
      plus all new tests from §11.
- [ ] Security review checklist (§12) fully verified by test (caplog-based
      secret-leak assertions, matching the 5F precedent).

---

## 14. Out-of-Scope Confirmation

Confirmed excluded from this sprint, per §14 of the brief: Sprint 6B, new
dissertation capabilities, new web dashboard, autonomous arbitrary shell
execution, rewriting Executive Control Tower, replacing 9Router, a new
Night Shift scheduler, a generic workflow-engine rewrite, a provider
billing-optimization engine, dynamic ML-based provider selection, unlimited
retry logic. Additionally out of scope by this document's own analysis:
full Codex/Claude CLI session integration (adapters ship as safe stubs;
real session wiring is a follow-up), async status/cancel on the provider
adapter contract, and any change to `google_workspace/` internals.

---

## 15. Codex Implementation Order

1. `app/providers/outcomes.py` — typed taxonomy + mapping (pure, no
   behavior change; safe standalone PR).
2. Additive audit schema migration (`provider_id`,
   `initial_provider_id`/`final_provider_id`, `resolved_model_label`,
   `skipped` status) + `models.py` field additions.
3. Extend `selection.py`/`service.py` to operate over provider chains,
   still 9Router-only chains at this step (regression-safe intermediate —
   full suite must stay green with *no* behavior change yet for existing
   single-provider config).
4. Add `is_available()` to the adapter contract; fix the circuit/skip
   attempt-accounting gap (G6).
5. Add `CodexAdapter`/`ClaudeAdapter` stubs + config plumbing
   (`app/config.py`, `.env.example`).
6. Add `ProviderGatewayAgentAdapter` (Dispatch seam) + new
   `AgentRegistry` entries for coding/architecture/generic roles.
7. Wire Night Shift job-type routes to the new agent entries
   (`_JOB_TYPE_ROUTES` additions only).
8. Extend `/providerstatus` for chain visibility; update
   `docs/executive-control-tower.md` and
   `docs/agent-dispatch-and-approvals.md`.
9. Full test suite for scenarios A–L; confirm regression baseline
   (556 → new total, all green).

Each step is intended to land as an independently reviewable, regression-safe
PR — no step requires a later step to keep the suite green.

---

## 16. Risks / Technical Debt

- **Specialist adapters ship as stubs.** Until real Codex/Claude session
  integration exists, Coding and Architecture will *in practice* always
  fall back to 9Router — correct and safe, but not yet "true" specialist-
  first behavior. Must be communicated as a known limitation, not silently
  implied as complete (matches this repo's own documentation culture, e.g.
  5F's "Known limitations" section).
- **In-memory circuit breaker** is per-process, non-persistent — a restart
  clears breaker state. Acceptable for the current single-instance
  deployment model (matches Sprint 5A's "single-instance protection"
  posture) but a real limitation if NOVA ever runs multi-process.
- **`fallback_group` string-matching** for combo scoping is simple and
  sufficient at today's scale (5 workflow categories); would need
  revisiting if the number of distinct combos grows significantly.
- **Google Workspace reasoning-vs-mutation boundary** is the single
  highest-risk correctness surface in this design — a bug here could make
  NOVA claim a Workspace action happened when it only generated text.
  Deserves the most scrutiny in implementation review and its own dedicated
  test beyond scenario D.
- **9Router alias opacity is permanent**, not a temporary gap — every
  future sprint touching `app/providers/registry.py` must re-affirm AD-13
  rather than assume alias identity has since become knowable.

---

## 17. Final Verdict

**READY FOR CODEX IMPLEMENTATION**

Rationale: every required primitive (bounded fallback loop, circuit
breaker, typed error categories, additive audit schema, deterministic
router/classifier, approval-gated dispatch seam) already exists in the
codebase in a form this sprint extends rather than replaces. All 14
architecture decisions are answered with concrete, evidence-grounded
designs that reuse existing authoritative components (§3, §AD-14) and
touch no forbidden boundary (`app/dispatch/` public interface,
`JOB_TRANSITIONS`, Control Tower schema, Google Workspace internals). The
one area of genuine new-build risk — direct specialist adapters — is
explicitly scoped down to safe stubs for this implementation pass,
eliminating the main source of uncertainty. Proceed via the sequencing in
§15, each step independently regression-tested against the 556-test
baseline.
