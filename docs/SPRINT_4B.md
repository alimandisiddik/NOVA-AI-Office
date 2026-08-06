# Sprint 4B — Deterministic Provider Model Fallback

## Status

✅ **Complete** — 2026-08-05

---

## Objective

Extend the Sprint 4A secure read-only provider gateway with deterministic,
registry-backed model selection and bounded automatic fallback among approved
9Router models. Sprint 4B preserves Sprint 3A routing/risk policy and Sprint
4A’s no-secret, read-only gateway controls.

## Architecture

```text
Telegram /ask
  → Sprint 3A Router (workflow + role + risk)
  → ProviderGatewayService
  → Model Registry + Selection Policy
  → One NineRouterAdapter endpoint/credential set
  → provider_request_audit + provider_request_attempts
```

### Registry and selection

`app/providers/registry.py` defines each registered model with:

- `model_id`, `provider_id`, `priority`, `enabled`, and `fallback_group`;
- supported Sprint 3A roles and workflows.

`app/providers/selection.py` uses `NOVA_PROVIDER_MODEL_PRIORITY` as the
authoritative deterministic order. It filters models that are disabled,
outside the configured allowlist, unsupported for the request workflow/role,
or currently open in that model’s independent circuit breaker. Fallback
candidates stay in the fallback group selected by the first eligible model.

## Fallback Behavior

- Each eligible model is attempted **exactly once**.
- A request makes at most **three** outbound model attempts.
- No per-model retry loop remains; Sprint 4A retry behavior is superseded.
- Automatic fallback requires **no repeated user confirmation**.
- Fallback occurs only for structured `timeout_error`, `connection_error`,
  and `rate_limit_error`. The NineRouter adapter maps HTTP `502`, `503`, and
  `504` to `connection_error`.
- Generic `ProviderError` stops immediately. HTTP `500`, `501`, `505`, `506`,
  and any other unlisted 5xx therefore do not fall back.
- Sensitive and destructive/high-risk prompts remain rejected before any
  outbound provider call.

## Per-Model Circuit Breakers

Circuit state is held in memory independently for each `model_id`:
`closed`, `open`, and `half_open`. A failure for one model never opens or
resets another model’s circuit.

## Additive Audit Migration

Sprint 4B adds `provider_request_attempts` for per-attempt metadata and adds
these fields to `provider_request_audit`:

- `initial_model_id`, `final_model_id`, `attempt_count`;
- `fallback_used`, `fallback_reason`.

`execution_id` remains nullable for direct `/ask` requests. New databases
enforce `UNIQUE(request_id, attempt_number)` for attempt rows. Existing Sprint
4A databases are migrated additively: existing audit rows remain valid and a
second initialization is idempotent.

No raw prompt, response, API key, header, provider URL, or provider error body
is stored in audit data.

## Safe Provider Status

`/providerstatus` presents only redacted operational metadata:

- configured model priority;
- enabled/disabled registry state per configured model;
- independent circuit state per model;
- most recent successful model;
- last fallback-reason category.

It does not expose credentials, raw URLs, headers, request content, responses,
or provider error bodies.

## Validation

- Registry/selection tests cover workflow, role, disabled, open-circuit,
  no-eligible, deterministic, and fallback-group paths.
- Gateway tests cover configuration validation, duplicate prevention,
  sequential fallback, 500/501/505/506 stop rules, 502/503/504 fallback,
  audit migration, and attempt uniqueness.
- All provider HTTP tests use `httpx.MockTransport`; automated tests make no
  real network calls.

## Known Limitations

1. The model registry is static code configuration for Sprint 4B.
2. Circuit-breaker state is in-memory and resets on process restart.
3. One 9Router endpoint and credential set is shared by all models.
4. Generation remains read-only text only: no streaming, tools, files, or
   autonomous multi-agent execution.

## Rollback Guidance

Remove `NOVA_PROVIDER_MODEL_PRIORITY` to leave the gateway unconfigured, or
remove the Sprint 4B provider modules during a reviewed rollback. Existing
provider audit tables are preserved and remain inert.
