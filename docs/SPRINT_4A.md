# Sprint 4A — Secure Read-Only Provider Gateway

## Status

✅ **Complete**

---

## Objective

Build a secure provider-agnostic gateway for read-only text generation through one external provider (9Router pilot).

## Scope Delivered
- `ProviderAdapter` asynchronous protocol
- `ProviderGatewayService` with routing/risk/circuit-breaker logic
- `NineRouterAdapter` using `httpx.AsyncClient` targeting OpenAI `/v1/chat/completions` API
- Role/workflow-based model deterministic selection
- Environment-based API credentials (`NOVA_PROVIDER_*`)
- Timeout, bounded retry, and output limit enforcement
- `provider_request_audit` additive schema migration with optional `execution_id`
- Sanitized audit metadata tracking (request ID, model ID, sizes, latency, status, errors)
- Telegram commands: `/ask <prompt>`, `/providerstatus`

## Constraints Upheld
- `httpx` added with bounds. No other dependencies.
- No real network calls in automated tests (mocked using `httpx.MockTransport`).
- No shell, subprocess, filesystem, or Git execution.
- Sensitive content rejected before outbound calls.
- Destructive requests rejected (requires `HIGH` risk approval, blocked on `/ask`).
- Secrets do not leak in tests, database, logs, or Telegram (/providerstatus redacts url).
- Cross-host HTTP redirects disabled.

## Limitations
- One pilot provider (NineRouter/OpenAI-compatible).
- Text generation only; no streaming, uploads, or tool calling.
- In-memory circuit breaker.
- `/ask` bypasses `ExecutionService` and does not persist an execution row.

## Rollback Guidance
- Unset `NOVA_PROVIDER_BASE_URL` in environment variables. Gateway will bypass initialization safely.
