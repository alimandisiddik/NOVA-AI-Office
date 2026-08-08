"""In-memory DTOs for the deterministic executive brief."""

from __future__ import annotations

from dataclasses import dataclass

from app.control_tower.models import Approval, Decision, WorkItem
from app.knowledge.models import KnowledgeResult
from app.nightshift.models import MorningBrief as NightShiftMorningBrief


@dataclass(frozen=True)
class BriefItem:
    """A live, read-only view of canonical NOVA operational state."""

    generated_at: str
    active_priorities: list[WorkItem]
    completed_since_last_brief: list[WorkItem]
    waiting_for_decision: list[Decision]
    blockers: list[WorkItem]
    approvals_pending: list[Approval]
    next_actions: dict[str, str]
    night_shift_summary: NightShiftMorningBrief | None
    owners: dict[str, str]
    active_work: list[WorkItem]
    knowledge_context: list[KnowledgeResult]
    recent_decisions: list[Decision]
