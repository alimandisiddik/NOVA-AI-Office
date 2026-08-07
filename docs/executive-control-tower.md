# NOVA Executive Control Tower

The Control Tower is NOVA’s metadata-only Chief of Staff layer.

## Commands

- `/capture category title` captures safe, bounded work metadata and a stored
  route recommendation.
- `/today` shows at most ten deterministic priorities.
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

## Provider-backed dispatch (Sprint 5G)

Provider-backed work uses the existing `DispatchService` seam through the
registered Coding, Architecture, and Generic AI agents. Provider selection and
fallback live only in `ProviderGatewayService`; the Control Tower gains no
second router or orchestration path. Google Workspace mutations remain on their
real Workspace services and their existing approval boundary.
