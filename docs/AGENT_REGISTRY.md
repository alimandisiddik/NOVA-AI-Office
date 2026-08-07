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

| Agent ID | Display name | Category | Capabilities |
|---|---|---|---|
| `document_agent` | Document Agent | `document` | `read_only`, `draft_only` |
| `presentation_agent` | Presentation Agent | `presentation` | `read_only`, `draft_only` |
| `procurement_agent` | Procurement Agent | `procurement` | `read_only`, `draft_only`, `external_communication` |
| `policy_agent` | Policy Agent | `policy` | `read_only`, `draft_only`, `publication` |
| `academic_agent` | Academic Agent | `academic` | `read_only`, `draft_only` |
| `development_agent` | Development Agent | `development` | `read_only`, `draft_only` |
| `workspace_agent` | Workspace Agent | `workspace`, `administrative`, `personal_planning` | `read_only`, `draft_only` |
| `night_shift_agent` | Night Shift Agent | `night_shift` | `read_only`, `draft_only` |
