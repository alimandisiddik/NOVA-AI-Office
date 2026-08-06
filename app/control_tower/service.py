"""Safe orchestration service for the Executive Control Tower MVP."""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.control_tower.models import (
    APPROVED_CATEGORIES, WORK_ITEM_STATES, Approval, Decision, MorningBrief,
    NightShiftEligibility, ShutdownSummary, WorkItem,
)
from app.control_tower.repository import (
    ConflictError, ControlTowerError, ControlTowerRepository, InvalidCategoryError,
    InvalidStateTransitionError, ValidationError, utc_now,
)
from app.control_tower.schema import apply_schema
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.security import SENSITIVE_CONTENT_PATTERN

if TYPE_CHECKING:
    from app.execution.service import ExecutionService
    from app.nightshift.service import NightShiftService

LOGGER = logging.getLogger(__name__)
JAKARTA = ZoneInfo("Asia/Jakarta")
MAX_TITLE_LENGTH = 200
MAX_TEXT_LENGTH = 2_000
MAX_TODAY_ITEMS = 10
WORK_ITEM_TRANSITIONS = {
    "inbox": frozenset({"clarification_needed", "planned", "in_progress", "deferred", "cancelled"}),
    "clarification_needed": frozenset({"planned", "in_progress", "deferred", "cancelled"}),
    "planned": frozenset({"in_progress", "awaiting_approval", "deferred", "cancelled"}),
    "in_progress": frozenset({"awaiting_approval", "completed", "deferred", "cancelled"}),
    "awaiting_approval": frozenset({"in_progress", "completed", "deferred", "cancelled"}),
    "deferred": frozenset({"planned", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
}
ROUTES = {
    "document": "Document Agent", "presentation": "Presentation Agent",
    "procurement": "Procurement Agent", "policy": "Policy Agent",
    "academic": "Academic Agent", "development": "Development Agent",
    "workspace": "Workspace Agent", "night_shift": "Night Shift Agent",
    "administrative": "Workspace Agent", "personal_planning": "Workspace Agent",
}


class ControlTowerService:
    def __init__(self, database: MemoryDatabase, *, execution: ExecutionService | None = None,
                 night_shift: NightShiftService | None = None) -> None:
        self.database = database
        self.repository = ControlTowerRepository(database)
        self.execution = execution
        self.night_shift = night_shift

    def initialize(self) -> None:
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
        except sqlite3.Error as error:
            raise MemoryDatabaseError("Control Tower schema initialization failed") from error

    @staticmethod
    def _clean_text(value: str | None, field: str, *, required: bool = False, limit: int = MAX_TEXT_LENGTH) -> str | None:
        if value is None:
            if required:
                raise ValidationError(f"{field} is required")
            return None
        if not isinstance(value, str):
            raise ValidationError(f"{field} is invalid")
        value = value.strip()
        if required and not value:
            raise ValidationError(f"{field} is required")
        if len(value) > limit:
            raise ValidationError(f"{field} is too long")
        if any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValidationError(f"{field} contains invalid characters")
        if SENSITIVE_CONTENT_PATTERN.search(value):
            raise ValidationError("Sensitive content is not permitted")
        return value

    @staticmethod
    def _priority_value(value: int, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
            raise ValidationError(f"{field} must be an integer from 0 to 10")
        return value

    @staticmethod
    def _deadline(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as error:
            raise ValidationError("Deadline must be ISO 8601") from error
        if parsed.tzinfo is None:
            # A date-only / naive local value deliberately means Jakarta local time.
            parsed = parsed.replace(tzinfo=JAKARTA)
        return parsed.astimezone(timezone.utc).isoformat()

    def _validate_dependencies(self, item_id: str, dependencies: list[str]) -> list[str]:
        if len(dependencies) != len(set(dependencies)):
            raise ValidationError("Duplicate dependencies are not allowed")
        for dependency in dependencies:
            if not isinstance(dependency, str) or not dependency.strip():
                raise ValidationError("Dependency is invalid")
            if dependency == item_id:
                raise ValidationError("A work item cannot depend on itself")
            if self.repository.get_work_item(dependency) is None:
                raise ValidationError("A dependency was not found")
        return dependencies

    def capture_work(self, category: str, title: str, actor: str, *, project_id: int | None = None,
                     summary: str | None = None, urgency: int = 0, importance: int = 0,
                     deadline: str | None = None, dependencies: list[str] | None = None,
                     clarification_needs: str | None = None, correlation_id: str | None = None) -> WorkItem:
        if category not in APPROVED_CATEGORIES:
            raise InvalidCategoryError("Category requires clarification")
        clean_title = self._clean_text(title, "Title", required=True, limit=MAX_TITLE_LENGTH)
        clean_summary = self._clean_text(summary, "Summary")
        clean_clarification = self._clean_text(clarification_needs, "Clarification needs")
        if project_id is not None and (isinstance(project_id, bool) or not isinstance(project_id, int) or not self.repository.project_exists(project_id)):
            raise ValidationError("Project was not found")
        item_id = str(uuid.uuid4())
        dependency_ids = self._validate_dependencies(item_id, list(dependencies or []))
        route = self.recommend_route(category)
        normalized_deadline = self._deadline(deadline)
        now = utc_now()
        item = WorkItem(
            item_id=item_id, project_id=project_id, category=category, title=clean_title or "",
            summary=clean_summary, urgency=self._priority_value(urgency, "Urgency"),
            importance=self._priority_value(importance, "Importance"), deadline=normalized_deadline,
            dependencies=dependency_ids, clarification_needs=clean_clarification,
            recommended_route=route, status="inbox", priority_score=0, created_at=now, updated_at=now,
        )
        scored = WorkItem(**{**item.__dict__, "priority_score": self.priority_score(item)})
        self.repository.create_work_item(scored, actor=actor, correlation_id=correlation_id)
        return scored

    def transition_work_item(self, item_id: str, new_status: str, actor: str, reason: str,
                             expected_current_status: str | None = None, correlation_id: str | None = None) -> None:
        self._clean_text(reason, "Reason", required=True, limit=500)
        if new_status not in WORK_ITEM_STATES:
            raise InvalidStateTransitionError("Unknown work item state")
        item = self.repository.get_work_item(item_id)
        if item is None:
            raise ValidationError("Work item was not found")
        expected = expected_current_status or item.status
        if expected != item.status:
            raise ConflictError("Work item was updated elsewhere")
        if new_status not in WORK_ITEM_TRANSITIONS[item.status]:
            raise InvalidStateTransitionError("Requested state transition is not allowed")
        self.repository.transition_work_item(item_id, expected_status=item.status, new_status=new_status,
                                             actor=actor, correlation_id=correlation_id)

    def unresolved_blocker_count(self, item: WorkItem) -> int:
        # completed and cancelled dependencies are resolved; every other state blocks.
        return sum(status not in {"completed", "cancelled"} for status in self.repository.dependency_statuses(item.item_id))

    def priority_score(self, item: WorkItem, *, now: datetime | None = None) -> int:
        score = item.urgency * 2 + item.importance * 3
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if item.deadline:
            deadline = datetime.fromisoformat(item.deadline).astimezone(timezone.utc)
            hours = (deadline - current).total_seconds() / 3600
            score += 60 if hours < 0 else 40 if hours <= 24 else 15 if hours <= 7 * 24 else 0
        score -= self.unresolved_blocker_count(item) * 12
        if item.status == "awaiting_approval":
            score += 10
        return score

    def get_today_priorities(self, limit: int = MAX_TODAY_ITEMS) -> list[WorkItem]:
        if not isinstance(limit, int) or limit < 1:
            raise ValidationError("Limit must be positive")
        items = self.repository.list_work_items(["inbox", "clarification_needed", "planned", "in_progress", "awaiting_approval"])
        rescored = [WorkItem(**{**item.__dict__, "priority_score": self.priority_score(item)}) for item in items]
        return sorted(rescored, key=lambda item: (-item.priority_score, item.deadline or "9999", item.created_at, item.item_id))[:min(limit, MAX_TODAY_ITEMS)]

    def recommend_route(self, category: str) -> str:
        try:
            return ROUTES[category]
        except KeyError as error:
            raise InvalidCategoryError("Category requires clarification") from error

    def register_decision(self, summary: str, rationale: str, impact: str, approved_by: str, actor: str,
                          *, project_id: int | None = None, supersedes: str | None = None,
                          correlation_id: str | None = None) -> Decision:
        if project_id is not None and not self.repository.project_exists(project_id):
            raise ValidationError("Project was not found")
        if supersedes and self.repository.get_decision(supersedes) is None:
            raise ValidationError("Superseded decision was not found")
        decision_id = str(uuid.uuid4())
        if supersedes == decision_id:
            raise ValidationError("A decision cannot supersede itself")
        now = utc_now()
        decision = Decision(decision_id, project_id, self._clean_text(summary, "Decision summary", required=True, limit=MAX_TITLE_LENGTH) or "",
                            self._clean_text(rationale, "Rationale", required=True) or "",
                            self._clean_text(impact, "Impact", required=True) or "",
                            self._clean_text(approved_by, "Approved by", required=True, limit=200) or "",
                            now, "active", supersedes, None, now)
        self.repository.create_decision(decision, actor=actor, correlation_id=correlation_id)
        return decision

    def request_approval(self, source_system: str, source_item_id: str, requested_action: str, actor: str, *, correlation_id: str | None = None) -> Approval:
        source = self._clean_text(source_system, "Approval source", required=True, limit=100) or ""
        identifier = self._clean_text(source_item_id, "Approval item", required=True, limit=200) or ""
        action = self._clean_text(requested_action, "Requested action", required=True, limit=500) or ""
        approval = Approval(str(uuid.uuid4()), source, identifier, action, "pending", utc_now())
        self.repository.create_approval_link(approval, actor=actor, correlation_id=correlation_id)
        return approval

    def resolve_approval(self, approval_id: str, approved: bool, actor: str) -> None:
        self.repository.resolve_approval_link(approval_id, status="approved" if approved else "rejected", actor=actor)

    def list_approvals(self) -> list[Approval]:
        approvals = self.repository.list_pending_link_approvals()
        for item in self.repository.list_work_items(["awaiting_approval"]):
            approvals.append(Approval(f"work:{item.item_id}", "control_tower", item.item_id, f"Review work item: {item.title}", "pending", item.updated_at))
        try:
            with self.database.connection() as connection:
                for row in connection.execute("SELECT id, created_at FROM executions WHERE state = 'awaiting_approval' ORDER BY id"):
                    approvals.append(Approval(f"execution:{row['id']}", "execution", str(row["id"]), "Review requested execution", "pending", row["created_at"]))
        except sqlite3.Error:
            LOGGER.warning("Approval aggregation: execution source unavailable; results are incomplete.")
        try:
            with self.database.connection() as connection:
                for row in connection.execute("SELECT job_id, job_type, requested_at FROM night_queue_jobs WHERE status = 'awaiting_approval' OR approval_required = 1 AND status = 'queued' ORDER BY requested_at, job_id"):
                    approvals.append(Approval(f"night_shift:{row['job_id']}", "night_shift", row["job_id"], f"Review Night Shift item: {row['job_type']}", "pending", row["requested_at"]))
        except sqlite3.Error:
            LOGGER.warning("Approval aggregation: night shift source unavailable; results are incomplete.")
        self.repository.audit_aggregation("approval_aggregation")
        return sorted({(item.source_system, item.source_item_id): item for item in approvals}.values(), key=lambda item: (item.requested_at, item.source_system, item.source_item_id))

    def evening_shutdown(self) -> ShutdownSummary:
        local_day = datetime.now(JAKARTA).date()
        all_items = self.repository.list_work_items()
        completed = [item for item in all_items if item.status == "completed" and datetime.fromisoformat(item.updated_at).astimezone(JAKARTA).date() == local_day]
        in_progress = [item for item in all_items if item.status == "in_progress"]
        tomorrow = [item for item in all_items if item.status in {"inbox", "planned", "deferred"}]
        awaiting = [item for item in all_items if item.status == "awaiting_approval"]
        clarification = [item for item in all_items if item.status == "clarification_needed"]
        queued = 0
        if self.night_shift is not None:
            queued = len([job for job in self.night_shift.list_night_jobs() if job.status in {"queued", "awaiting_approval", "draft_saved"}])
        eligibility = [item for item in tomorrow if item.category == "night_shift" and self.unresolved_blocker_count(item) == 0]
        self.repository.audit_aggregation("shutdown")
        return ShutdownSummary(completed, in_progress, tomorrow, awaiting, clarification,
                               NightShiftEligibility(len(eligibility), queued), bool(eligibility or queued))

    def morning_brief(self) -> MorningBrief:
        latest = self.night_shift.get_latest_morning_brief() if self.night_shift is not None else None
        failed_safe = [item for item in self.repository.list_work_items(["deferred"])]
        candidates = self.repository.list_work_items(["clarification_needed", "awaiting_approval"])
        decisions = [Decision(f"candidate:{item.item_id}", item.project_id, item.title, "Decision candidate captured as work item", "Review required", "", "", "pending", None, None, item.created_at) for item in candidates if item.category in {"policy", "procurement", "administrative"}]
        self.repository.audit_aggregation("morning_aggregation")
        return MorningBrief(self.get_today_priorities(limit=5), latest, self.list_approvals(), failed_safe, decisions)
