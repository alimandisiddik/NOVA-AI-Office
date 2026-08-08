# Sprint 7D Acceptance Matrix

| Acceptance criterion | Evidence |
| --- | --- |
| Assignment truth is isolated from `WorkItem` | `app/agent_assignment/` owns its additive SQLite tables; no Control Tower model or schema changes. |
| Registered agent/capability validation fails closed | `AgentAssignmentService.propose_assignment()` delegates to the existing `AgentRegistry.validate_capability()` before persistence. |
| Assignment lifecycle is auditable | Every create/transition writes one row to `agent_assignment_audit_log` in the same transaction. |
| Dispatch is canonical execution | `start_execution()` calls public `DispatchService` and only stores its linked `dispatch_id`; no dispatch/approval SQL is present. |
| Approval-required capability remains gated | `start_execution()` creates/gets the canonical approval and does not execute the dispatch while it awaits approval. |
| 7A read surface is frozen and minimal | `get_active_assignment_summary()` returns only `AssignmentSummary` with assignment, agent, derived operator, and status. |
| Operator labels remain derived | `resolve_operator()` maps current adapters to `CONTROL_TOWER`, `CODEX`, `CLAUDE`, or `NINEROUTER`; `GEMINI` is reserved and safely unavailable. |
| Telegram remains read-only | `/assignments` and `/assignmentstatus` only list/read assignment records behind the existing authorization guard. |
