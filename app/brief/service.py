"""Deterministic, provider-independent executive brief composition."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.brief.models import BriefItem
from app.control_tower.models import Decision, WorkItem
from app.control_tower.service import ControlTowerService
from app.knowledge.models import KnowledgeResult
from app.knowledge.service import KnowledgeService
from app.nightshift.service import NightShiftService

JAKARTA = ZoneInfo("Asia/Jakarta")
ACTIVE_STATUSES = ["inbox", "clarification_needed", "planned", "in_progress", "awaiting_approval"]
DECISION_CANDIDATE_STATUSES = ["clarification_needed", "awaiting_approval"]
DECISION_CANDIDATE_CATEGORIES = {"policy", "procurement", "administrative"}
MAX_ACTIVE_WORK = 8
MAX_COMPLETED = 5
MAX_BLOCKERS = 5
MAX_APPROVALS = 5
MAX_KNOWLEDGE_CONTEXT = 4
MAX_DECISIONS = 4


class ExecutiveBriefService:
    """Compose bounded presentation data using canonical read-only services.

    Only pre-existing public read methods of ``ControlTowerService`` (and its
    already-public ``repository`` attribute) are called here — this package
    must never edit ``app/control_tower/**`` (7A-exclusive territory, frozen
    by docs/SPRINT_7C.md Sec.13).
    """

    def __init__(
        self,
        control_tower: ControlTowerService,
        *,
        night_shift: NightShiftService | None = None,
        knowledge: KnowledgeService | None = None,
    ) -> None:
        self.control_tower = control_tower
        self.night_shift = night_shift
        self.knowledge = knowledge

    def generate_morning_brief(self, now: datetime | None = None) -> BriefItem:
        """Generate a stable live brief without writes, providers, or network calls."""
        local_now = (now or datetime.now(JAKARTA)).astimezone(JAKARTA)
        active_work = self.control_tower.get_today_priorities(limit=MAX_ACTIVE_WORK)
        all_active = self.control_tower.repository.list_work_items(ACTIVE_STATUSES)
        completed = self._completed_today(local_now)
        blockers = [item for item in all_active if self.control_tower.unresolved_blocker_count(item) > 0]
        blockers.sort(key=self._work_item_order)
        approvals = self.control_tower.list_approvals()[:MAX_APPROVALS]
        waiting_for_decision = self._pending_decision_candidates()
        recent_decisions = self._recent_decisions(active_work)
        owners = {item.item_id: self.control_tower.owner_for(item) for item in active_work}
        next_actions = {item.item_id: self.control_tower.next_action_for(item) for item in active_work}
        related_knowledge = self._related_knowledge(active_work)
        night_shift_summary = self.night_shift.get_latest_morning_brief() if self.night_shift is not None else None

        return BriefItem(
            generated_at=local_now.date().isoformat(),
            active_priorities=active_work,
            completed_since_last_brief=completed,
            waiting_for_decision=waiting_for_decision[:MAX_DECISIONS],
            blockers=blockers[:MAX_BLOCKERS],
            approvals_pending=approvals,
            next_actions=next_actions,
            night_shift_summary=night_shift_summary,
            owners=owners,
            active_work=active_work,
            knowledge_context=related_knowledge,
            recent_decisions=recent_decisions[:MAX_DECISIONS],
        )

    def _completed_today(self, local_now: datetime) -> list[WorkItem]:
        completed = [
            item
            for item in self.control_tower.repository.list_work_items(["completed"])
            if datetime.fromisoformat(item.updated_at).astimezone(JAKARTA).date() == local_now.date()
        ]
        return sorted(completed, key=lambda item: (item.updated_at, item.item_id), reverse=True)[:MAX_COMPLETED]

    def _pending_decision_candidates(self) -> list[Decision]:
        """Mirrors ControlTowerService.morning_brief()'s own candidate-decision synthesis
        (same categories, same "pending" candidate shape) without editing control_tower."""
        candidates = self.control_tower.repository.list_work_items(DECISION_CANDIDATE_STATUSES)
        decisions = [
            Decision(
                f"candidate:{item.item_id}", item.project_id, item.title,
                "Decision candidate captured as work item", "Review required", "", "",
                "pending", None, None, item.created_at,
            )
            for item in candidates
            if item.category in DECISION_CANDIDATE_CATEGORIES
        ]
        return sorted(decisions, key=lambda decision: (decision.created_at, decision.decision_id))

    def _recent_decisions(self, active_work: list[WorkItem]) -> list[Decision]:
        project_ids = sorted({item.project_id for item in active_work if item.project_id is not None})
        decisions: list[Decision] = []
        for project_id in project_ids:
            decisions.extend(self.control_tower.repository.list_decisions_for_project(project_id))
        active = [decision for decision in decisions if decision.status == "active"]
        return sorted(active, key=lambda decision: (decision.created_at, decision.decision_id), reverse=True)

    def _related_knowledge(self, active_work: list[WorkItem]) -> list[KnowledgeResult]:
        if self.knowledge is None or not active_work:
            return []
        work_item_ids = {item.item_id for item in active_work}
        project_ids = {item.project_id for item in active_work if item.project_id is not None}
        related = [
            result
            for result in self.knowledge.query()
            if result.item.work_item_id in work_item_ids or result.item.project_id in project_ids
        ]
        return related[:MAX_KNOWLEDGE_CONTEXT]

    @staticmethod
    def _work_item_order(item: WorkItem) -> tuple[int, str, str, str]:
        return (-item.priority_score, item.deadline or "9999", item.created_at, item.item_id)
