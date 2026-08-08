"""Read-only composition boundary for the Executive Dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, TypeVar

from app.agent_assignment.models import AgentAssignment
from app.agent_assignment.service import AgentAssignmentService
from app.brief.models import BriefItem
from app.brief.service import ExecutiveBriefService
from app.control_tower.models import Approval, Decision, WorkItem
from app.control_tower.service import ControlTowerService
from app.knowledge.models import KnowledgeResult
from app.knowledge.service import KnowledgeService
from app.memory.models import Project
from app.memory.services import WorkspaceMemoryService
from app.nightshift.models import RuntimeModeState
from app.nightshift.service import NightShiftService

ACTIVE_WORK_STATUSES = ("inbox", "clarification_needed", "planned", "in_progress", "awaiting_approval")
ACTIVE_ASSIGNMENT_STATUSES = ("proposed", "accepted", "in_progress")
MAX_PROJECTS = 12
MAX_ACTIVE_WORK = 12
MAX_APPROVALS = 8
MAX_DECISIONS = 8
MAX_ASSIGNMENTS = 12
MAX_KNOWLEDGE = 8
_T = TypeVar("_T")


@dataclass(frozen=True)
class DashboardSnapshot:
    generated_at: str
    projects: list[Project]
    active_work_items: list[WorkItem]
    pending_approvals: list[Approval]
    pending_decisions: list[Decision]
    agent_assignments: list[AgentAssignment]
    knowledge: list[KnowledgeResult]
    night_shift_mode: RuntimeModeState | None
    provider_health: str
    morning_brief: BriefItem | None
    errors: tuple[str, ...] = ()


class DashboardService:
    """Composes bounded dashboard view data without mutating NOVA state."""

    def __init__(
        self,
        control_tower: ControlTowerService,
        *,
        memory: WorkspaceMemoryService | None = None,
        agent_assignments: AgentAssignmentService | None = None,
        knowledge: KnowledgeService | None = None,
        night_shift: NightShiftService | None = None,
        executive_brief: ExecutiveBriefService | None = None,
        provider_health: str = "Not configured",
    ) -> None:
        self.control_tower = control_tower
        self.memory = memory
        self.agent_assignments = agent_assignments
        self.knowledge = knowledge
        self.night_shift = night_shift
        self.executive_brief = executive_brief
        self.provider_health = provider_health

    def snapshot(self, now: datetime | None = None) -> DashboardSnapshot:
        """Return a best-effort read-only snapshot safe to render locally."""
        errors: list[str] = []
        projects = self._read("Projects", lambda: self.memory.list_projects()[:MAX_PROJECTS], [], errors)
        active_work_items = self._read(
            "Active work",
            lambda: self.control_tower.get_today_priorities(limit=MAX_ACTIVE_WORK),
            [],
            errors,
        )
        brief = self._read(
            "Executive brief", lambda: self._generate_brief(now), None, errors
        )
        pending_approvals = brief.approvals_pending[:MAX_APPROVALS] if brief is not None else []
        pending_decisions = brief.waiting_for_decision[:MAX_DECISIONS] if brief is not None else []
        assignments = self._read(
            "Agent assignments",
            lambda: self._active_assignments()[:MAX_ASSIGNMENTS],
            [],
            errors,
        )
        knowledge = self._read(
            "Knowledge", lambda: self.knowledge.query()[:MAX_KNOWLEDGE], [], errors
        )
        night_shift_mode = self._read(
            "Night Shift", lambda: self.night_shift.get_runtime_mode(), None, errors
        )
        generated_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        return DashboardSnapshot(
            generated_at=generated_at,
            projects=projects,
            active_work_items=active_work_items,
            pending_approvals=pending_approvals,
            pending_decisions=pending_decisions,
            agent_assignments=assignments,
            knowledge=knowledge,
            night_shift_mode=night_shift_mode,
            provider_health=self.provider_health,
            morning_brief=brief,
            errors=tuple(errors),
        )

    def _active_assignments(self) -> list[AgentAssignment]:
        if self.agent_assignments is None:
            return []
        assignments: list[AgentAssignment] = []
        for status in ACTIVE_ASSIGNMENT_STATUSES:
            assignments.extend(self.agent_assignments.list_assignments(status=status, limit=MAX_ASSIGNMENTS))
        return sorted(assignments, key=lambda item: (item.updated_at, item.assignment_id), reverse=True)

    def _generate_brief(self, now: datetime | None) -> BriefItem | None:
        if self.executive_brief is None:
            return None
        # Calls the real ControlTowerService.list_approvals() (via
        # ExecutiveBriefService), which appends exactly one row to
        # control_tower_audit_log per call (operation="approval_aggregation"),
        # the same audit trail write that already occurs on every /morning or
        # /execbrief invocation. An earlier version of this method routed
        # through a local read-only shim that skipped that audit write by
        # calling only `repository.list_pending_link_approvals()` directly —
        # but that silently dropped four of list_approvals()'s five approval
        # sources (awaiting_approval work items, executions, night-shift
        # jobs, dispatch approvals), understating pending approvals on the
        # one panel this dashboard exists to get right. Reusing the real,
        # canonical aggregation avoids duplicating 7A-owned cross-domain
        # composition logic (and the drift risk that caused the original
        # bug) at the cost of one documented, harmless audit-log append per
        # page load. See docs/executive-dashboard.md.
        return self.executive_brief.generate_morning_brief(now)

    @staticmethod
    def _read(label: str, operation: Callable[[], _T], fallback: _T, errors: list[str]) -> _T:
        try:
            return operation()
        except Exception:
            errors.append(f"{label} is temporarily unavailable.")
            return fallback
