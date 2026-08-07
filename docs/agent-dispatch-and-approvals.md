# Agent Dispatch & Approval Operations

## Status

Sprint 5B.1 is under implementation review. It provides the Wave 3 seam that
future Night Shift automation consumes; it does not create a Night Shift worker
or make real provider, shell, Git, Drive, Calendar, or messaging calls.

## Controlled flow

1. `DispatchService.create_dispatch()` validates the source, static agent, and
   closed capability allowlist before any row is persisted.
2. `read_only` and `draft_only` dispatches start as `pending`. The registered
   `external_communication` and `publication` capabilities start as
   `awaiting_approval` with a canonical `ApprovalRequest` created in the same
   transaction.
3. The configured Telegram owner may `/approve` or `/reject` a requested
   approval. Approval creates `approved`; rejection creates `rejected`; neither
   path can revive a terminal dispatch.
4. `/canceldispatch` writes `cancelled` and closes any requested approval in the
   same transaction. A later decision is rejected as stale.
5. The non-side-effecting `LocalDeterministicAgentAdapter` returns a structured
   `DispatchResult`. Dispatches can finish as `succeeded`, `failed`, or
   `timed_out`; no raw provider response or exception is persisted.

## Telegram operations

`build_application()` receives `dispatch` and `approvals` explicitly and stores
those injected services in `Application.bot_data`. `_dispatch_svc()` and
`_approval_svc()` are read-only accessors; there are no mutable module globals.

- `/dispatch <agent_id> <capability> [payload_ref]`
- `/dispatches`
- `/dispatchstatus <dispatch_id>`
- `/approve <approval_id>`
- `/reject <approval_id> [reason]`
- `/canceldispatch <dispatch_id>`
- `/retrydispatch <dispatch_id>`

Replies use bounded, sanitized public identifiers. Invalid syntax and missing
service injection fail without stack traces, SQL, filesystem paths, or secrets.

## Data safety

`payload_ref` is persisted as a bounded opaque reference only. It is never a
raw prompt, shell command, provider body, file content, or secret. All user
supplied free text that can be persisted—including payload references,
approval actions, closure/rejection reasons, result summaries, and metadata—is
validated against `app.security.SENSITIVE_CONTENT_PATTERN` before writes; key
and SSH/private-key shaped values are also rejected.
