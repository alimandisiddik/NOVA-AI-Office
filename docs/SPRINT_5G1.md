# Sprint 5G.1 — 9Router Upstream Route Mapping Runtime Fix

## Status: Implemented, tested against `httpx.MockTransport` — **not yet
validated against a live 9Router deployment**. Do not mark this remediation
complete until an operator confirms it against real 9Router traffic.

## 1. Root cause

Sprint 5G's `NineRouterAdapter.generate_text()` sent `request.model_id` —
NOVA's own internal route alias (`nova-v1`, `nova-v1-coding`,
`nova-v1-review`, ...) — directly as the `model` field of
`POST /v1/chat/completions`. 9Router does not recognize NOVA's internal
aliases as combo IDs.

Confirmed runtime evidence:

- `GET <9Router base>/v1/models` → HTTP 200, listing 9Router's own combo
  IDs: `general`, `Development`, `review`, `Academic`, `Policy`, `Fast`,
  `academic-f`, `policy-f`, `Design`, `Content`, `presentation-studio`.
- `POST /v1/chat/completions` with `model="general"` → HTTP 200, content
  `NOVA_OK`, 9Router-reported resolved model `gemini-pro-default`.
- NOVA's `/ask` (sending `model="nova-v1"`) → HTTP 404.

9Router itself, auth, base URL, the endpoint, and combo routing all work.
Only NOVA's alias-to-route translation was missing — this is exactly the
AD-13 boundary Sprint 5G's own frozen specification (`docs/SPRINT_5G.md`)
required ("NOVA internal route alias must remain distinct from actual
provider/upstream model or combo identity") but the initial implementation
did not actually build.

## 2. Architecture decision

Three identities, previously conflated into one (`model_id`), are now
explicit and independently traceable end-to-end:

| Identity | Example | Where it lives | Sent upstream? |
|---|---|---|---|
| NOVA-internal route alias | `nova-v1` | `RegisteredModel.model_id`, `ProviderRequest.model_id`, `ProviderRequestAttempt.model_id`, `ProviderAuditRecord.{initial,final}_model_id` | Never |
| Upstream/provider route | `general` | `RegisteredModel.upstream_route_id`, `ProviderCandidate.upstream_route_id`, `ProviderRequest.upstream_route_id`, `ProviderRequestAttempt.upstream_route_id`, `ProviderAuditRecord.{initial,final}_upstream_route_id` | **Yes** — this is what `NineRouterAdapter` sends as `model` |
| Actual resolved model | `gemini-pro-default` | `ProviderResponse.model_id`, `ProviderAuditRecord.resolved_model_label` | n/a (received, not sent) |

The narrowest correct seam: the mapping lives as data in
`app/providers/registry.py` (baked-in, evidenced defaults) with an optional
runtime override (`NOVA_PROVIDER_UPSTREAM_ROUTE_MAP`), resolved once per
candidate in `app/providers/selection.py::resolve_upstream_route_id()`, and
consumed only at the adapter boundary (`NineRouterAdapter`). No new router,
no Control Tower business logic, no change to `app/router/` or
`app/control_tower/`.

**Fail-closed by design, at two layers:**

1. **Selection**: `select_provider_chain()` excludes any 9Router-provider
   candidate lacking a resolved upstream route from the chain entirely — it
   is never selected as an attempt, so it can never be sent upstream by
   alias.
2. **Adapter**: `NineRouterAdapter.generate_text()` additionally refuses to
   make a network call at all if `request.upstream_route_id` is falsy,
   raising `ConfigurationError` — defense in depth in case a future caller
   ever bypasses selection.

Neither layer ever falls back to sending the internal alias upstream — the
exact defect being fixed.

## 3. Exact files changed

- `app/providers/registry.py` — `RegisteredModel.upstream_route_id: str | None = None`
  (additive field); evidenced defaults set for `nova-v1` (`general`),
  `nova-v1-coding` (`Development`), `nova-v1-review` (`review`),
  `nova-v1-fast` (`Fast`, inferred by name/tier pairing — see §4);
  `nova-v1-fallback` mapped to `general` as a deliberate backward-compat
  exception (see §4); `nova-v1-coding-fallback`, `nova-v1-review-fallback`,
  `nova-v1-isolated`, `nova-v2-preview` left unmapped (`None`).
- `app/providers/selection.py` — `ProviderCandidate.upstream_route_id` field;
  new `resolve_upstream_route_id()`; `select_provider_chain()` accepts
  `upstream_route_overrides` and excludes unmapped 9Router candidates.
- `app/providers/models.py` — `ProviderRequest.upstream_route_id`,
  `ProviderRequestAttempt.upstream_route_id`,
  `ProviderAuditRecord.{initial,final}_upstream_route_id` (all additive,
  defaulted).
- `app/providers/ninerouter.py` — sends `request.upstream_route_id` (not
  `model_id`) as `model`; raises `ConfigurationError` if absent; falls back
  to the upstream route (not the internal alias) when the response omits
  `model`.
- `app/providers/service.py` — threads `upstream_route_overrides` through
  the constructor and into `select_provider_chain`; carries
  `candidate.upstream_route_id` through every `ProviderRequest`/
  `ProviderRequestAttempt` construction and into `_audit()`; rejects a base
  URL ending in `/v1` at `_validate_config()`.
- `app/providers/schema.py`, `app/providers/repository.py` — additive
  migration: `provider_request_attempts.upstream_route_id`,
  `provider_request_audit.{initial,final}_upstream_route_id`.
- `app/config.py` — `NOVA_PROVIDER_UPSTREAM_ROUTE_MAP` parsing into
  `Settings.nova_provider_upstream_route_map` (`dict[str, str]`).
- `app/main.py` — passes the parsed map into `ProviderGatewayService` as
  `upstream_route_overrides`.
- `.env.example` — new `NOVA_PROVIDER_UPSTREAM_ROUTE_MAP` (illustrative,
  non-secret route IDs only) and clarified `NOVA_PROVIDER_BASE_URL`
  host-root contract.
- `app/telegram_bot.py` — `/providerstatus` now shows
  `alias=<internal> -> upstream_route=<route or UNCONFIGURED>` per entry,
  across every configured combo group, never conflating the two.
- `tests/test_provider_gateway.py`, `tests/test_provider_policy.py`,
  `tests/test_provider_telegram.py` — updated to assert the corrected
  wire behavior (upstream route sent, not internal alias) and to supply
  explicit `upstream_route_overrides` where a test's own intent is attempt
  accounting rather than mapping policy.
- `tests/test_provider_upstream_routing.py` (new) — the dedicated Sprint
  5G.1 test suite (13 tests, see §7).

**Explicitly not touched**: `app/control_tower/`, `app/router/`,
`app/dispatch/service.py`, `app/dispatch/schema.py`,
`app/nightshift/service.py` (`JOB_TRANSITIONS`), the Night Shift scheduler,
queue, or approval policy, `app/google_workspace/`, Dissertation.

## 4. Exact mapping / configuration model

Registry defaults (`app/providers/registry.py`), all evidenced or
explicitly justified:

| NOVA alias | Upstream route | Basis |
|---|---|---|
| `nova-v1` | `general` | Runtime-evidenced: `POST` with `model="general"` → HTTP 200 |
| `nova-v1-fallback` | `general` | **Deliberate exception** — pre-existing (Sprint 4A/4B) alias with established test/behavioral precedent; no distinct upstream fallback combo is evidenced, but leaving it unmapped would silently disable long-standing generic fallback behavior. Points at the same real, evidenced `general` combo (not a fabricated identity) rather than a guessed one. |
| `nova-v1-fast` | `Fast` | `Fast` confirmed to exist (`GET /v1/models`); not curl-verified end-to-end like the three above, but the only justified inference available (name/tier pairing) — explicitly *not* assigned to coding/review, which the frozen spec forbids as arbitrary |
| `nova-v1-coding` | `Development` | Runtime-evidenced: listed in `GET /v1/models` |
| `nova-v1-coding-fallback` | *(unmapped)* | No distinct evidenced upstream combo; introduced by Sprint 5G with zero prior test/behavioral precedent — left unconfigured rather than fabricated |
| `nova-v1-review` | `review` | Runtime-evidenced: listed in `GET /v1/models` |
| `nova-v1-review-fallback` | *(unmapped)* | Same reasoning as `nova-v1-coding-fallback` |
| `nova-v1-isolated`, `nova-v2-preview` | *(unmapped)* | No evidenced correspondence; `nova-v2-preview` is disabled regardless |
| `codex-direct`, `claude-direct` | n/a | Not 9Router-routed — direct specialist stubs never consult this field |

Overridable via `NOVA_PROVIDER_UPSTREAM_ROUTE_MAP` (comma-separated
`alias:route` pairs), which always wins over the registry default — for
operators whose 9Router deployment uses different combo names, or who want
to configure a genuinely distinct fallback combo for the coding/review
aliases once one exists.

## 5. Audit identity separation

`provider_request_attempts` now carries `model_id` (internal alias) and
`upstream_route_id` (provider route) as two independent columns per attempt
row. `provider_request_audit` carries `{initial,final}_model_id`,
`{initial,final}_upstream_route_id`, and `resolved_model_label` — three
independent pairs/fields, so a single audit row can answer, for example:
"NOVA selected `nova-v1-coding` (alias), dispatched it to `Development`
(upstream route), and 9Router actually served `gemini-pro-default`
(resolved model)" without any of those three values ever overwriting or
standing in for another. All additions are additive columns on the existing
Sprint 4A–5G schema; no existing column, table, or semantics changed. No
secrets, raw prompts, or full responses are added to the audit trail —
unchanged posture from Sprint 5G.

## 6. Base URL contract

`NineRouterAdapter` always appends `/v1/chat/completions` to `base_url`.
`base_url` must therefore be the 9Router **host root** only (e.g.
`https://api.9router.com` or `http://localhost:20128`) — never including a
trailing `/v1`, which would silently double it
(`.../v1/v1/chat/completions`) and 404. `ProviderGatewayService._validate_config()`
now rejects a base URL ending in `/v1` at startup, and `.env.example`
documents the contract explicitly. No change to the endpoint path itself —
runtime evidence proved `/v1/chat/completions` works correctly once the
`model` field carries a real upstream route.

## 7. Tests

`tests/test_provider_upstream_routing.py` (13 tests, new):

1. `test_nova_v1_maps_to_general` — internal alias → upstream route.
2. `test_selection_preserves_internal_alias_alongside_upstream_route` —
   both identities readable independently off the same selected candidate.
3. `test_adapter_sends_upstream_route_not_internal_alias` — wire payload
   asserted directly via `httpx.MockTransport`.
4. `test_coding_route_maps_to_development`.
5. `test_review_route_maps_to_review`.
6. `test_audit_records_all_three_distinct_identities` /
   `test_adapter_falls_back_to_upstream_route_when_response_omits_model` —
   resolved model recorded separately from both the alias and the route.
7. `test_adapter_refuses_to_send_without_an_upstream_route` /
   `test_gateway_fails_safely_when_only_unmapped_aliases_are_configured` —
   unconfigured route fails safely, deterministically, with no network call.
8. `test_no_secrets_logged_on_upstream_routing_failure` — the configured
   API key never appears in any captured log record.
9–11, 13. Full existing suite (provider fallback, Dispatch, Night Shift)
   re-run and green — see §Q evidence in the final report.
12. `tests/test_provider_telegram.py::test_providerstatus_success_and_redaction`
    (updated) — asserts `/providerstatus` output distinguishes
    `alias=<x> -> upstream_route=<y>` per entry.

Plus targeted updates to three pre-existing test files
(`test_provider_gateway.py`, `test_provider_policy.py`,
`test_provider_telegram.py`) where the old assertions encoded the pre-fix
(buggy) wire behavior — see the final report for exactly what changed and
why each change is a correction, not a weakening.

## 8. Out of scope (confirmed unchanged)

Executive Control Tower orchestration semantics, `app/router/` business
classification, `DispatchService`'s public interface, Night Shift
`JOB_TRANSITIONS`/scheduler/queue/approval policy, Google Workspace
execution, Dissertation functionality, Sprint 6B. No subprocess, no shell
execution, no secret exposure anywhere in this change.
