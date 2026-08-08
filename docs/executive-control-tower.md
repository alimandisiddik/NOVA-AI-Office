# NOVA Executive Control Tower

The Control Tower is NOVA’s metadata-only Chief of Staff layer.

## Commands

- `/capture category title [| project name]` captures safe, bounded work
  metadata, optionally links an existing Workspace Memory project, and stores
  a route recommendation. It never creates projects implicitly.
- `/today` shows at most ten deterministic priorities with status, derived
  owner, and derived next action.
- `/workitem <item_id>` shows one work item's status, derived owner, next
  action, dependencies, and currently recorded decisions.
- `/decision Project | decision | rationale` preserves Workspace Memory’s legacy
  decision register. `/decision summary` records a Control Tower decision.
- `/approvals` combines Control Tower, Execution, and Night Shift approval views.
- `/shutdown` reports Jakarta-day progress and Night Shift eligibility without
  dispatching work.
- `/morning` combines priorities, the actual latest Night Shift brief when
  available, approvals, failed-safe items, and derived decision candidates.

All Telegram errors are sanitized. Missing injection returns a safe availability
message; domain and unexpected failures never return exception strings.

## Lifecycle and priority

Valid states are `inbox`, `clarification_needed`, `planned`, `in_progress`,
`awaiting_approval`, `completed`, `deferred`, and `cancelled`. Terminal states
cannot reopen. Transitions use compare-and-swap persistence and transactional
state/audit writes.

Priority is deterministic: user urgency and importance, overdue/proximate
UTC-normalized deadline, unresolved dependency blockers, and approval waiting
state. Ties use deadline, creation timestamp, and item ID.

## Executive workflow

`WorkItem` remains the canonical workflow record. Owner and next action are
computed at read time: owner falls back to the stored route recommendation and
may use an injected assignment summary when the Agent Assignment capability is
available; next action is derived from the work item state and dependencies.
Neither value is persisted on `WorkItem`, and Control Tower never owns or
writes agent-assignment state.

## Sprint 7A acceptance matrix

| Acceptance criterion | Implementation | Verification |
|---|---|---|
| Capture links an existing project and rejects an unknown one | `/capture` resolves an optional pipe-delimited project name before creating the `WorkItem` | `test_capture_with_existing_project_links_work_item`; `test_capture_with_unknown_project_rejects_without_partial_write` |
| Workflow views show status, owner, and next action | `/today` and `/workitem` render computed workflow reads | `test_today_and_workitem_show_workflow_details` |
| Workflow works without Agent Assignment | `owner_for()` falls back to `recommended_route`; `next_action_for()` uses only `WorkItem` state and dependencies | `test_owner_for_falls_back_to_recommended_route`; `test_next_action_for_every_work_item_state` |
| Assignment integration remains read-only | `owner_for()` consumes only `get_active_assignment_summary()` and derives the displayed owner | `test_owner_for_uses_frozen_assignment_summary` |
| No Control Tower schema change | The workflow capability adds no persisted fields or migrations | `git diff -- app/control_tower/schema.py` is empty |

## Provider-backed dispatch (Sprint 5G)

Provider-backed work uses the existing `DispatchService` seam through the
registered Coding, Architecture, and Generic AI agents. Provider selection and
fallback live only in `ProviderGatewayService`; the Control Tower gains no
second router or orchestration path. Google Workspace mutations remain on their
real Workspace services and their existing approval boundary.
