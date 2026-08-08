"""AgentAssignment state machine layered on top of the dispatch seam."""

from __future__ import annotations

import re
import sqlite3
import uuid

from app.agent_assignment.errors import (
    AssignmentStaleUpdateError,
    AssignmentTransitionError,
    AssignmentValidationError,
)
from app.agent_assignment.models import AgentAssignment, AssignmentSummary
from app.agent_assignment.operators import resolve_operator
from app.agent_assignment.repository import AgentAssignmentRepository, utc_now
from app.agent_assignment.schema import apply_schema
from app.dispatch.approvals import ApprovalService
from app.dispatch.models import DispatchRequest
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.security import SENSITIVE_CONTENT_PATTERN


_APPROVAL_REQUIRED = frozenset({"external_communication", "publication"})
_TRANSITIONS = {
    "proposed": frozenset({"accepted", "cancelled"}),
    "accepted": frozenset({"in_progress", "cancelled", "reassigned"}),
    "in_progress": frozenset({"completed", "reassigned"}),
}
_OPAQUE_BLOCKLIST = re.compile(r"(?:ssh-(?:rsa|ed25519)|-----BEGIN)", re.IGNORECASE)


class AgentAssignmentService:
    def __init__(
        self,
        database: MemoryDatabase,
        *,
        registry: AgentRegistry,
        dispatch: DispatchService,
        approvals: ApprovalService,
    ) -> None:
        self.database = database
        self.repository = AgentAssignmentRepository(database)
        self.registry = registry
        self.dispatch = dispatch
        self.approvals = approvals

    def initialize(self) -> None:
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
        except sqlite3.Error as error:
            raise MemoryDatabaseError("Agent assignment schema initialization failed") from error

    @staticmethod
    def _safe(value: str, field: str, *, allow_empty: bool = False, limit: int = 200) -> str:
        if not isinstance(value, str):
            raise AssignmentValidationError("Assignment request is invalid.")
        cleaned = value.strip()
        if (
            (not cleaned and not allow_empty)
            or len(cleaned) > limit
            or SENSITIVE_CONTENT_PATTERN.search(cleaned)
            or _OPAQUE_BLOCKLIST.search(cleaned)
        ):
            raise AssignmentValidationError("Sensitive or invalid assignment content was rejected.")
        return cleaned

    def _validated_request(self, work_item_id: str, requested_capability: str, agent_id: str, actor: str) -> tuple[str, str, str, str]:
        safe_work_item_id = self._safe(work_item_id, "work item id")
        safe_capability = self._safe(requested_capability, "requested capability")
        safe_agent_id = self._safe(agent_id, "agent id")
        safe_actor = self._safe(actor, "actor")
        self.registry.validate_capability(safe_agent_id, safe_capability)
        return safe_work_item_id, safe_capability, safe_agent_id, safe_actor

    def _transition(
        self,
        assignment: AgentAssignment,
        target_status: str,
        actor: str,
        *,
        event: str,
        detail: str = "",
        dispatch_id: str | None = None,
    ) -> AgentAssignment:
        if target_status not in _TRANSITIONS.get(assignment.status, frozenset()):
            raise AssignmentTransitionError("Assignment state transition is not allowed.")
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self.repository.transition_in(
                connection,
                assignment.assignment_id,
                assignment.status,
                target_status,
                actor,
                event=event,
                detail=detail,
                dispatch_id=dispatch_id,
            ):
                raise AssignmentStaleUpdateError("Assignment update is stale.")
            return self.repository.get_assignment_in(connection, assignment.assignment_id)

    def propose_assignment(
        self, work_item_id: str, requested_capability: str, agent_id: str, actor: str
    ) -> AgentAssignment:
        work_item_id, requested_capability, agent_id, actor = self._validated_request(
            work_item_id, requested_capability, agent_id, actor
        )
        now = utc_now()
        assignment = AgentAssignment(
            str(uuid.uuid4()), work_item_id, requested_capability, agent_id, "proposed", None, actor, now, now
        )
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.repository.insert_assignment_in(connection, assignment, actor)
        return assignment

    def accept_assignment(self, assignment_id: str, actor: str) -> AgentAssignment:
        assignment = self.repository.get_assignment(self._safe(assignment_id, "assignment id"))
        return self._transition(assignment, "accepted", self._safe(actor, "actor"), event="accepted")

    def start_execution(self, assignment_id: str, actor: str) -> AgentAssignment:
        assignment = self.repository.get_assignment(self._safe(assignment_id, "assignment id"))
        safe_actor = self._safe(actor, "actor")
        if "in_progress" not in _TRANSITIONS.get(assignment.status, frozenset()):
            raise AssignmentTransitionError("Assignment state transition is not allowed.")
        request = DispatchRequest(
            "control_tower_work_item",
            assignment.work_item_id,
            assignment.assigned_agent_id,
            assignment.requested_capability,
            f"assignment:{assignment.assignment_id}",
            f"assignment:{assignment.assignment_id}",
            safe_actor,
            correlation_id=assignment.assignment_id,
        )
        dispatch = self.dispatch.create_dispatch(request, safe_actor)
        if assignment.requested_capability in _APPROVAL_REQUIRED:
            self.approvals.request_approval(
                dispatch.dispatch_id,
                f"Approve {assignment.requested_capability} assignment {assignment.assignment_id[:8]}",
                safe_actor,
                correlation_id=assignment.assignment_id,
            )
        else:
            self.dispatch.dispatch(dispatch.dispatch_id, actor=safe_actor)
        return self._transition(
            assignment,
            "in_progress",
            safe_actor,
            event="execution_started",
            dispatch_id=dispatch.dispatch_id,
        )

    def complete_assignment(self, assignment_id: str, actor: str) -> AgentAssignment:
        assignment = self.repository.get_assignment(self._safe(assignment_id, "assignment id"))
        return self._transition(assignment, "completed", self._safe(actor, "actor"), event="completed")

    def cancel_assignment(self, assignment_id: str, actor: str, reason: str) -> AgentAssignment:
        assignment = self.repository.get_assignment(self._safe(assignment_id, "assignment id"))
        return self._transition(
            assignment,
            "cancelled",
            self._safe(actor, "actor"),
            event="cancelled",
            detail=self._safe(reason, "reason", allow_empty=True, limit=500),
        )

    def reassign(self, assignment_id: str, new_agent_id: str, actor: str) -> AgentAssignment:
        assignment = self.repository.get_assignment(self._safe(assignment_id, "assignment id"))
        safe_actor = self._safe(actor, "actor")
        safe_agent_id = self._safe(new_agent_id, "agent id")
        self.registry.validate_capability(safe_agent_id, assignment.requested_capability)
        if "reassigned" not in _TRANSITIONS.get(assignment.status, frozenset()):
            raise AssignmentTransitionError("Assignment state transition is not allowed.")
        now = utc_now()
        replacement = AgentAssignment(
            str(uuid.uuid4()), assignment.work_item_id, assignment.requested_capability,
            safe_agent_id, "proposed", None, safe_actor, now, now
        )
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self.repository.transition_in(
                connection,
                assignment.assignment_id,
                assignment.status,
                "reassigned",
                safe_actor,
                event="reassigned",
                detail=f"replacement:{replacement.assignment_id}",
            ):
                raise AssignmentStaleUpdateError("Assignment update is stale.")
            self.repository.insert_assignment_in(
                connection, replacement, safe_actor, detail=f"replaces:{assignment.assignment_id}"
            )
        return replacement

    def get_assignment(self, assignment_id: str) -> AgentAssignment:
        return self.repository.get_assignment(self._safe(assignment_id, "assignment id"))

    def list_assignments(self, *, status: str | None = None, limit: int = 20) -> list[AgentAssignment]:
        if status is not None and status not in {"proposed", "accepted", "in_progress", "completed", "cancelled", "reassigned"}:
            raise AssignmentValidationError("Assignment status is invalid.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise AssignmentValidationError("Assignment limit is invalid.")
        return self.repository.list_assignments(status=status, limit=limit)

    def get_active_assignment_summary(self, work_item_id: str) -> AssignmentSummary | None:
        assignment = self.repository.get_active_for_work_item(self._safe(work_item_id, "work item id"))
        if assignment is None:
            return None
        return AssignmentSummary(
            assignment.assignment_id,
            assignment.assigned_agent_id,
            resolve_operator(assignment.assigned_agent_id),
            assignment.status,
        )
