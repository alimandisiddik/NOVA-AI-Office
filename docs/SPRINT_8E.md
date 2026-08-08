# Sprint 8E — Workspace Write Safety MVP

## Status: **REVISED AND READY TO FREEZE**

This is the human-approved Sprint 8E architecture revision after G3 Stage 3
integration. It supersedes every earlier 8E proposal that treated the active
Workspace write surface as a broad multi-action sprint. Stage 4 implementation
has not started. This document is a contract revision only; it introduces no
runtime change and does not authorize a production Google write.

The MVP proves one complete, safe external-write spine only:

```text
PreparedWorkspaceAction(docs_memo)
  -> ready_for_action/current revision
  -> WorkspaceAction(create_docs_file)
  -> DispatchService -> ApprovalService -> exact user confirmation
  -> freshness + integrity validation -> CAS claim
  -> typed DocsWriteService -> succeeded | outcome_unknown
```

**Core invariant: no valid approval = no Google write.**

## 1. Scope

### In scope

- One executable action type: `create_docs_file`.
- One source type: a current, `ready_for_action` 8C `docs_memo`.
- Private Google Docs creation only; no sharing, recipients, permissions, or
  existing-document edit.
- `WorkspaceAction` persistence, canonical payload fingerprint, independent
  8C freshness validation, approval binding, CAS execution claim, typed
  `DocsWriteService`, metadata-only audit, and `outcome_unknown` handling.
- Explicit Telegram action-specific approval and read-only status UX.
- Fail-closed Docs capability/runtime wiring with no OAuth or network activity
  during application startup.

### Explicitly out of scope

- Gmail send/reply/draft; Calendar create/update; Sheets or Slides writes;
  Docs edits; Drive sharing, permissions, or deletion; Google Keep; bulk or
  autonomous actions; generic Google API execution; automatic provider retry.
- All earlier proposed 8E actions remain future-roadmap vocabulary only and
  are **not** 8E acceptance scope.
- A real Workspace write in automated tests.

## 2. 8C lineage and freshness — AD-8E-01

### Findings

`PreparedWorkspaceAction` is immutable in content. `DraftingService.revise()`
creates a replacement record whose `supersedes_id` points to its immediate
predecessor, then atomically changes that predecessor from `prepared` to
`superseded`. Only a `prepared` record can be revised. The model has no
`revision_number`, `parent_id`, or `successor_id`; a revision chain is encoded
by successor rows referencing their predecessor. `source_ref` is optional for
`docs_memo`, so it cannot be used as a safe general lineage key.

The existing public `get_ready_action(action_id)` returns a row only if that
row's status is `ready_for_action`; it does not independently prove that no
successor row supersedes it. Today, revising a ready action is rejected, which
makes the stale-ready case unreachable through the current UI. That incidental
restriction is not a durable 8E safety proof and must not become the execution
contract.

### Frozen minimum rule

Before action creation **and again immediately before the provider call**, 8E
must obtain the source through an additive public 8C seam:

```text
get_current_ready_action(action_id) -> PreparedWorkspaceAction | None
```

It returns a record only when all of the following hold in the 8C domain:

1. `id == action_id`, `content_type == "docs_memo"`, and
   `status == "ready_for_action"`.
2. No `PreparedWorkspaceAction` has `supersedes_id == action_id`.
3. The returned immutable row is the canonical source used to build the
   execution snapshot.

This is a small additive 8C compatibility extension, not an 8E raw-SQL join
or a cross-domain repository query. It may be implemented by 8C using its own
repository and transaction boundary. A later 8C policy that permits revising a
ready action must atomically supersede the old row before returning its
replacement. Therefore, approval of revision N can never execute after N+1
supersedes N.

**Integrity and freshness are distinct:** fingerprint comparison proves the
exact approved snapshot; `get_current_ready_action()` proves the snapshot is
still the current executable revision.

## 3. Action and dispatch contract — AD-8E-05

`WorkspaceAction` is the 8E aggregate and its sole action vocabulary is:

```text
action_type = create_docs_file
```

Creation maps one action to one backward-compatible dispatch:

| Dispatch field | Frozen value |
|---|---|
| `source_type` | additive `workspace_action` |
| `source_id` | decimal `WorkspaceAction.id` |
| `agent_id` | `workspace_agent` |
| `capability` | additive `workspace_write` (approval-required) |
| `adapter_id` | `google_workspace_adapter` |
| `payload_ref` | `workspace_action:<id>` only — no body |
| `idempotency_key` | action's stable caller-provided key |
| `correlation_id` | propagated unchanged across action, dispatch, approval, audit, and provider-safe result |

`workspace_action` is appended to the existing dispatch source vocabulary and
schema check; existing source types are neither repurposed nor migrated.
`workspace_write` is a specific approval-required capability, not a re-label
of `control_tower_work_item`, `night_shift_job`, `telegram_direct`,
`external_communication`, or `publication`. Existing dispatch behavior remains
backward compatible.

**As-built at G4:** the registered `adapter_id` (`google_workspace_adapter`)
is reserved vocabulary only — no `GoogleWorkspaceAgentAdapter` executes it,
and `DispatchService.dispatch()`/`retry_dispatch()` are never called for a
`workspace_action` dispatch. `DispatchService`/`ApprovalService` provide only
the canonical create/approve/reject linkage and idempotency; the entire
execution CAS (`approved -> executing -> succeeded|failed|outcome_unknown`)
and the single `DocsWriteService` call live in `WorkspaceActionService`
itself (§5). This is deliberately safer than routing through generic
dispatch execution: it makes it structural, not just policy, that no
generic `/dispatch`/`/retrydispatch` path can ever reach a Docs write.

The action service alone creates this dispatch and its
approval linkage.

## 4. Exact approval snapshot and approval policy — AD-8E-06

The exact snapshot is deterministic canonical JSON (sorted keys, UTF-8,
compact separators) hashed with SHA-256. It contains:

- `schema_version`;
- `workspace_action_id` and `action_type`;
- `prepared_action_id` plus 8C identity (`id`, `content_type`, `status`, and
  `supersedes_id`); no invented revision number;
- exact `title` and exact `body_text` that will be created;
- authenticated `account_namespace` when available;
- `creation_mode: "private"` and `risk_classification`.

The body stays only in the immutable 8C record. `workspace_actions`,
`dispatches`, approval text, audit, status, and provider summaries must never
duplicate it. At execution, 8E rebuilds the snapshot exclusively from the
current 8C row and compares its fingerprint with the persisted fingerprint.

`create_docs_file` is classified `MODERATE_EXTERNAL_WRITE`: it is external,
private by default, manually deletable, has no third-party recipient, changes
no permission, and does not overwrite an existing resource. **The tier does
not remove approval:** every instance requires a valid canonical
`ApprovalService` approval bound to the exact `dispatch_id` and
`workspace_action_id`.

Approval expires exactly **15 minutes** after `requested_at`, calculated from
the injected application UTC clock; 8E must not silently inherit an unbounded
or database-local default. Approval eligibility requires all of: requested
status, authorized actor, unexpired timestamp, matching dispatch/action
binding, and matching current fingerprint.

Telegram must show a numbered action-specific contract, for example
`1. Create private Google Doc #<action-id>` and require the exact active
selection (or the existing exact `/approve <approval-id>` command once its
binding is rendered). Bare conversational `oke`, `ya`, `setuju`, `lanjut`, or
`approve` never authorizes this write. 8G's high-risk ambiguity protection is
preserved; no global natural-language affirmative mapping is added.

## 5. Lifecycle, CAS, retry, and recovery — AD-8E-02

The independent `WorkspaceAction` lifecycle is:

```text
prepared -> awaiting_approval -> approved -> executing -> succeeded
                                          \-> outcome_unknown
awaiting_approval -> rejected
prepared | awaiting_approval | approved -> cancelled
executing -> failed  (only when non-execution or provider failure is known)
```

Terminal states are `succeeded`, `failed`, `rejected`, `cancelled`, and
`outcome_unknown`. State changes require optimistic CAS on action `version`
and expected status. The following transitions must be atomic with their
corresponding dispatch/approval observation where applicable:

- creation: `prepared -> awaiting_approval` after durable dispatch and
  approval linkage;
- canonical approval observation: `awaiting_approval -> approved`;
- execution claim: `approved -> executing` with `(id, version, status)` CAS;
- finalization: `executing -> succeeded|failed|outcome_unknown` with CAS.

Only the CAS winner may call `DocsWriteService`; all losing/duplicate calls
must read the current result and make zero additional provider calls. The
MVP sets dispatch `max_attempts=1` and must not expose generic retry for a
Workspace action. `failed` is permitted only before provider submission or
when the provider's non-execution/failure is known. A timeout, transport loss,
or ambiguous response after submission is `outcome_unknown`, not `failed`.

`outcome_unknown` **never auto-retries** and is not a dead end. Read-only
status must say exactly in substance: `WRITE OUTCOME COULD NOT BE CONFIRMED.
DO NOT RETRY AUTOMATICALLY. MANUAL RECONCILIATION REQUIRED.` It safely shows
only action ID, action type, attempted UTC timestamp, correlation ID, and a
sanitized provider resource reference if one exists — never the document body.
An operator later needs an explicit, separately-approved admin-safe mechanism
to mark `verified_succeeded` or `verified_failed`; that reconciliation command
is mandatory debt before an irreversible future slice such as Gmail send.

## 6. Persistence, audit, clock, and retention

`workspace_actions` is additive and contains only: identity; `action_type`;
`prepared_action_id`; `dispatch_id`; `approval_id`; payload schema version and
fingerprint; status; idempotency key; `version`; correlation ID; requested,
approved, executing, and completed timestamps; sanitized provider resource
reference; and outcome classification. It contains no raw Docs body, OAuth
material, credentials, secrets, or raw provider response.

`workspace_action_audit_log` is append-only and metadata-only: action ID,
event, actor, sanitized detail/category, correlation ID, and UTC timestamp.
It must not store document content, OAuth tokens, credentials, secrets, or raw
provider responses. Audit events include request, approval requested/granted/
rejected/expired, freshness/integrity rejection, CAS winner/loss, provider
submission outcome, and reconciliation-required state.

All timestamps are UTC ISO-8601. Approval expiry and every action transition
use one application UTC clock abstraction injected/fakeable in tests; no local
time and no database implicit timestamp may decide expiry or state ordering.

Data minimization is intentional: 8C remains the canonical immutable content
store, `workspace_actions` stores its reference and fingerprint only, and 8E
needs no encrypted execution-payload envelope. Future Gmail recipients or
Drive grantees require a separate protected execution-payload and key-
management decision before implementation.

## 7. OAuth and capability strategy — AD-8E-03

The frozen authorization requirement is the **Docs create-and-populate
capability for a document NOVA creates**. The exact implementation is:

1. Google Docs `documents.create` creates the blank private/default-private
   document with the approved title.
2. Typed `DocsWriteService.populate_document_body(...)` invokes Google Docs
   `documents.batchUpdate` with an `InsertTextRequest` to insert the exact
   approved body into that newly created document.

For precisely those two methods and the app-created-document boundary,
`https://www.googleapis.com/auth/drive.file` is sufficient and is the frozen
least-privilege **write-scope choice** for this MVP. This does not claim that
`drive.file` is the only Google Docs authorization scope that can authorize
Docs operations generally. 8E must not add any scope beyond what is necessary
for `create_docs_file`: no Gmail, Calendar, Sheets, Slides, broad full-Drive,
Drive-sharing, or permission scope. Existing independently required identity
or read capabilities remain governed by their own existing contracts; 8E adds
no unrelated authorization capability. The resulting document remains
private/default-private because 8E performs no sharing or permission mutation.

`GoogleAuthenticator` must change its safe compatibility test from exact
scope-set equality to `required_scopes ⊆ granted_scopes`, while still
canonicalizing and rejecting unapproved scopes. Capability checks are per
operation and require the particular authorization capability; a token with an
allowed superset remains valid for its existing required capabilities and this
Docs capability. A missing Docs create-and-populate capability (implemented
here with `drive.file`) disables only `create_docs_file` and fails closed
before action execution; read-only NOVA remains available. One canonical token
store is sufficient for this MVP because the same authenticated account and
approved-scope validation remain authoritative; no dual-store design is
justified.

Startup must never call `reconnect()`, open a browser, refresh credentials, or
contact Google. Before live OAuth reliance, verify the Google Auth Platform
publishing/testing state and token-lifetime/refresh-token behavior in the
actual deployment environment; this document makes no unsupported lifetime
claim.

## 8. Runtime bundle and typed write boundary — AD-8E-04

Sprint 8E owns the small integration needed to make its own safe MVP usable;
no separate large prerequisite sprint is required. It constructs a real
configuration-aware Workspace bundle only when both existing OAuth paths are
configured together. Construction creates the canonical `GoogleAuthenticator`
and `GoogleClientFactory` but does not load credentials, refresh, invoke OAuth,
or make a network call. Missing configuration, absent token, invalid token, or
absent Docs-write capability leaves the write service unavailable while
application startup and existing read-only services continue.

The bundle must use the existing canonical authenticator/client factory — no
second credentials subsystem. `DocsWriteService` is typed and narrowly exposes
only `create_private_document(...)` and `populate_document_body(...)` for a
validated title/body. Those methods are backed only by `documents.create` and
`documents.batchUpdate`/`InsertTextRequest` for the newly created document;
they return only a sanitized resource reference/result. There is no generic
Google API passthrough and no write service for another Google product.

## 9. Security, regression, and documentation hygiene

- No valid approval, no freshness proof, no fingerprint match, no capability,
  or no CAS claim means zero Google calls.
- Google-side execution does not depend on an LLM/provider. Runtime errors are
  sanitized; provider bodies, credentials, and document content never enter
  logs or audit.
- `share_file` is removed from 8E. Future sharing should prefer the
  `drive.file`/app-created-resource boundary where possible; broad Drive
  permission scopes require separate security review.
- 8C remains preparation-only, 8D remains candidate-first, and 8G ambiguity
  controls remain intact. Existing dispatch and approval behavior is preserved.
- Architecture and review documents must not contain absolute home-directory
  paths, personal email addresses, OAuth client IDs, tokens, credentials, or
  other machine-specific personal identifiers unless technically required.
  Use repo-relative paths; retain useful technical provenance such as commit
  hashes.

## 10. Acceptance criteria

1. Only `create_docs_file` is executable in 8E.
2. Its source is a current ready 8C `docs_memo`.
3. A superseded revision cannot execute and makes zero Docs calls.
4. A valid canonical ApprovalService approval is required.
5. Approval is bound to the exact WorkspaceAction and dispatch.
6. The canonical snapshot fingerprint matches before execution.
7. Freshness is checked independently of the fingerprint.
8. Explicit numbered/action-specific confirmation is required.
9. Bare contextual affirmatives cannot authorize the action.
10. `approved -> executing` has a CAS claim.
11. Concurrent/duplicate execution makes exactly one provider call.
12. A typed DocsWriteService creates the exact approved private document.
13. No generic Google API passthrough exists.
14. Startup performs no OAuth/browser/network action.
15. Missing write scope fails closed without breaking reads.
16. No automatic retry follows provider-submission uncertainty.
17. Uncertain submission persists `outcome_unknown`.
18. `outcome_unknown` visibly requires reconciliation.
19. Audit is metadata-only.
20. No document body is duplicated in WorkspaceAction persistence or audit.
21. UTC injected clock governs expiry and timestamps.
22. Prior 8A–8D/8G behavior is preserved.
23. No non-Docs write scope is introduced.
24. Automated tests perform no real Workspace write.
25. Full regression passes.

## 11. Required test plan

- **Freshness:** ready N, approval, N+1 supersedes N, rejected execution, zero
  Docs calls.
- **Integrity:** changed execution snapshot/fingerprint, zero API calls.
- **Approval:** absent, rejected, expired, wrong actor, wrong action/dispatch,
  and valid approvals.
- **Conversation:** exact numbered confirmation; bare `oke`/`ya`/`setuju`
  rejected; duplicate Telegram update safe.
- **CAS:** two execute calls, one winner, one write invocation.
- **Outcome unknown:** simulated timeout after submission, no retry, persisted
  state, reconciliation-required status.
- **Auth:** no startup OAuth; reads work without write scope; Docs action fails
  closed without it; approved superset scope accepted.
- **Docs:** exact title/body, default-private result, sanitized output, no body
  in audit.
- **Regression:** 8C preparation-only, 8D candidate-first, 8G ambiguity,
  backward-compatible dispatch/approval, and full suite.

## 12. Implementation order

1. Add and test the 8C current-ready public contract.
2. Add WorkspaceAction model/schema/repository/CAS.
3. Add dispatch source/capability/adapter compatibility.
4. Bind approval to action and canonical snapshot.
5. Build fake `DocsWriteService` and action state machine.
6. Add `outcome_unknown`, status, and reconciliation visibility.
7. Add exact Telegram approval/status UX.
8. Make scope/capability/authenticator compatibility changes.
9. Add real typed `DocsWriteService`.
10. Wire the configuration-aware runtime bundle.
11. Run integration/security tests and full regression.
12. Obtain Claude independent review and human-approved live Docs smoke test.
13. Only then evaluate a subsequent write slice.

## 13. Remaining implementation prerequisites

- Implement the additive 8C `get_current_ready_action()` seam; 8E must not
  bypass it with raw SQL.
- Add a separately authorized reconciliation mechanism before any irreversible
  future action is enabled.
- Complete live-environment Google Auth Platform/token-lifetime verification
  before depending on long-running refresh tokens.
