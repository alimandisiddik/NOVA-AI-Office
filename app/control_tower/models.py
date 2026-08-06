"""Typed domain models for the NOVA Executive Control Tower."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

WORK_ITEM_STATES = frozenset({
    "inbox", "clarification_needed", "planned", "in_progress",
    "awaiting_approval", "completed", "deferred", "cancelled",
})
APPROVED_CATEGORIES = frozenset({
    "document", "presentation", "procurement", "policy", "academic",
    "development", "workspace", "night_shift", "administrative",
    "personal_planning",
})

@dataclass(frozen=True)
class WorkItem:
    item_id: str
    project_id: int | None
    category: str
    title: str
    summary: str | None
    priority_score: int
    urgency: int
    importance: int
    deadline: str | None
    dependencies: list[str]
    clarification_needs: str | None
    recommended_route: str | None
    status: str
    created_at: str
    updated_at: str

@dataclass(frozen=True)
class Decision:
    decision_id: str
    project_id: int | None
    summary: str
    rationale: str
    impact: str
    approved_by: str
    effective_date: str
    status: str
    supersedes: str | None
    superseded_by: str | None
    created_at: str

@dataclass(frozen=True)
class Approval:
    approval_id: str
    source_system: str
    source_item_id: str
    requested_action: str
    status: str
    requested_at: str
    resolved_at: str | None = None
    resolved_by: str | None = None

@dataclass(frozen=True)
class NightShiftEligibility:
    eligible_count: int
    total_queued: int

@dataclass(frozen=True)
class ShutdownSummary:
    completed_today: list[WorkItem]
    in_progress: list[WorkItem]
    moved_to_tomorrow: list[WorkItem]
    awaiting_approval: list[WorkItem]
    unresolved_clarification: list[WorkItem]
    night_shift_eligibility: NightShiftEligibility
    night_shift_required: bool

@dataclass(frozen=True)
class MorningBrief:
    today_priorities: list[WorkItem]
    latest_night_shift_brief: Any | None
    approval_queue: list[Approval]
    failed_safe_items: list[WorkItem]
    decisions_needed: list[Decision]
