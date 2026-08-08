"""Data-transfer objects owned by the AgentAssignment domain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentAssignment:
    assignment_id: str
    work_item_id: str
    requested_capability: str
    assigned_agent_id: str
    status: str
    dispatch_id: str | None
    requested_by: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AssignmentSummary:
    """Frozen, minimal 7D read DTO consumed by Sprint 7A only."""

    assignment_id: str
    assigned_agent_id: str
    operator_id: str
    status: str
