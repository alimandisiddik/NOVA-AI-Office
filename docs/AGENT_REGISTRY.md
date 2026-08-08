# Agent Registry

## Registry purpose

This document is the authoritative index for NOVA agent roles. It is intentionally empty of active agents in Sprint 1.1; roles should be added only after their responsibilities, constraints, permissions, and evaluation criteria are defined.

## Required fields for future agents

| Field | Description |
| --- | --- |
| Agent ID | Stable, machine-readable identifier. |
| Name | Human-readable role name. |
| Mission | The business outcome the agent supports. |
| Owner | Accountable human or team. |
| Inputs | Approved input types and trust boundaries. |
| Outputs | Expected artifacts and quality criteria. |
| Tools | Permitted services and integrations. |
| Permissions | Explicit access level and approval requirements. |
| Escalation | Conditions requiring a human decision. |
| Evaluation | Metrics, test cases, and review cadence. |
| Status | Proposed, active, paused, or retired. |

## Active agents

The Operator label is a derived, display-only assignment value. It is not an
agent identity, provider model, role, or stored `WorkItem` field. The current
registered dispatch adapters resolve safely as shown below; `GEMINI` remains
reserved and unavailable until a registered dispatch adapter exists.

| Agent ID | Display name | Category | Capabilities | Derived operator |
|---|---|---|---|---|
| `document_agent` | Document Agent | `document` | `read_only`, `draft_only` | `CONTROL_TOWER` |
| `presentation_agent` | Presentation Agent | `presentation` | `read_only`, `draft_only` | `CONTROL_TOWER` |
| `procurement_agent` | Procurement Agent | `procurement` | `read_only`, `draft_only`, `external_communication` | `CONTROL_TOWER` |
| `policy_agent` | Policy Agent | `policy` | `read_only`, `draft_only`, `publication` | `CONTROL_TOWER` |
| `academic_agent` | Academic Agent | `academic` | `read_only`, `draft_only` | `CONTROL_TOWER` |
| `development_agent` | Development Agent | `development` | `read_only`, `draft_only` | `CONTROL_TOWER` |
| `workspace_agent` | Workspace Agent | `workspace`, `administrative`, `personal_planning` | `read_only`, `draft_only` | `CONTROL_TOWER` |
| `night_shift_agent` | Night Shift Agent | `night_shift` | `read_only`, `draft_only` | `CONTROL_TOWER` |
| `coding_agent` | Coding Agent | `development` | `read_only`, `draft_only` | `CODEX` |
| `architecture_agent` | Architecture Agent | `development` | `read_only`, `draft_only` | `CLAUDE` |
| `generic_ai_agent` | Generic AI Agent | `general` | `read_only`, `draft_only` | `NINEROUTER` |
