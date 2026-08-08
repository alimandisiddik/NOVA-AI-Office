from __future__ import annotations

import pytest

from app.agent_assignment.errors import AssignmentTransitionError, AssignmentValidationError
from app.agent_assignment.operators import OPERATORS, resolve_operator
from app.agent_assignment.service import AgentAssignmentService
from app.dispatch.approvals import ApprovalService
from app.dispatch.errors import UnknownAgentError, UnsupportedCapabilityError
from app.dispatch.registry import AGENT_REGISTRY, AgentRegistry
from app.dispatch.service import DispatchService
from app.memory.database import MemoryDatabase


@pytest.fixture
def database(tmp_path) -> MemoryDatabase:
    return MemoryDatabase(tmp_path / "nova.sqlite3")


@pytest.fixture
def services(database: MemoryDatabase) -> tuple[AgentAssignmentService, DispatchService, ApprovalService]:
    registry = AgentRegistry()
    approvals = ApprovalService(database, authorized_user_id=7)
    dispatch = DispatchService(database, registry=registry, approvals=approvals)
    dispatch.initialize()
    assignments = AgentAssignmentService(
        database, registry=registry, dispatch=dispatch, approvals=approvals
    )
    assignments.initialize()
    return assignments, dispatch, approvals


def test_assignment_lifecycle_persists_linked_dispatch_and_audit(services, database: MemoryDatabase) -> None:
    assignments, dispatch, _ = services
    proposed = assignments.propose_assignment("work-1", "draft_only", "document_agent", "user:7")
    accepted = assignments.accept_assignment(proposed.assignment_id, "user:7")
    started = assignments.start_execution(accepted.assignment_id, "user:7")
    completed = assignments.complete_assignment(started.assignment_id, "user:7")

    assert proposed.status == "proposed"
    assert accepted.status == "accepted"
    assert started.status == "in_progress"
    assert started.dispatch_id is not None
    assert dispatch.get_dispatch(started.dispatch_id).status == "succeeded"
    assert completed.status == "completed"
    with database.connection() as connection:
        audit = connection.execute(
            "SELECT event, from_status, to_status FROM agent_assignment_audit_log "
            "WHERE assignment_id = ? ORDER BY id",
            (proposed.assignment_id,),
        ).fetchall()
    assert [tuple(row) for row in audit] == [
        ("proposed", None, "proposed"),
        ("accepted", "proposed", "accepted"),
        ("execution_started", "accepted", "in_progress"),
        ("completed", "in_progress", "completed"),
    ]


def test_invalid_agent_capability_and_sensitive_content_fail_before_persistence(services, database: MemoryDatabase) -> None:
    assignments, _, _ = services
    with pytest.raises(UnknownAgentError):
        assignments.propose_assignment("work-1", "draft_only", "unknown_agent", "user:7")
    with pytest.raises(UnsupportedCapabilityError):
        assignments.propose_assignment("work-1", "publication", "document_agent", "user:7")
    with pytest.raises(AssignmentValidationError):
        assignments.propose_assignment("work-1", "draft_only", "document_agent", "api_key=secret")
    with database.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM agent_assignments").fetchone()[0]
    assert count == 0


def test_forbidden_transitions_fail_closed(services) -> None:
    assignments, _, _ = services
    assignment = assignments.propose_assignment("work-1", "read_only", "document_agent", "user:7")
    with pytest.raises(AssignmentTransitionError):
        assignments.complete_assignment(assignment.assignment_id, "user:7")
    with pytest.raises(AssignmentTransitionError):
        assignments.start_execution(assignment.assignment_id, "user:7")
    assignments.cancel_assignment(assignment.assignment_id, "user:7", "not needed")
    with pytest.raises(AssignmentTransitionError):
        assignments.accept_assignment(assignment.assignment_id, "user:7")


def test_reassignment_closes_old_row_and_creates_new_proposal(services) -> None:
    assignments, _, _ = services
    first = assignments.propose_assignment("work-1", "draft_only", "document_agent", "user:7")
    assignments.accept_assignment(first.assignment_id, "user:7")
    replacement = assignments.reassign(first.assignment_id, "development_agent", "user:7")

    assert replacement.assignment_id != first.assignment_id
    assert replacement.status == "proposed"
    assert replacement.assigned_agent_id == "development_agent"
    assert assignments.get_assignment(first.assignment_id).status == "reassigned"


def test_approval_required_assignment_never_dispatches_before_approval(database: MemoryDatabase) -> None:
    registry = AgentRegistry()
    approvals = ApprovalService(database, authorized_user_id=7)
    dispatch = DispatchService(database, registry=registry, approvals=approvals)
    dispatch.initialize()
    assignments = AgentAssignmentService(
        database, registry=registry, dispatch=dispatch, approvals=approvals
    )
    assignments.initialize()
    assignment = assignments.propose_assignment(
        "work-approval", "external_communication", "procurement_agent", "user:7"
    )
    assignments.accept_assignment(assignment.assignment_id, "user:7")

    started = assignments.start_execution(assignment.assignment_id, "user:7")

    assert started.status == "in_progress"
    assert started.dispatch_id is not None
    assert dispatch.get_dispatch(started.dispatch_id).status == "awaiting_approval"
    pending = approvals.list_pending(source_type="control_tower_work_item")
    assert [item.dispatch_id for item in pending] == [started.dispatch_id]


def test_frozen_assignment_summary_is_minimal_and_active_only(services) -> None:
    assignments, _, _ = services
    assignment = assignments.propose_assignment("work-summary", "draft_only", "coding_agent", "user:7")
    summary = assignments.get_active_assignment_summary("work-summary")

    assert summary is not None
    assert (summary.assignment_id, summary.assigned_agent_id, summary.operator_id, summary.status) == (
        assignment.assignment_id,
        "coding_agent",
        "CODEX",
        "proposed",
    )
    assignments.cancel_assignment(assignment.assignment_id, "user:7", "changed priority")
    assert assignments.get_active_assignment_summary("work-summary") is None


def test_every_registered_agent_resolves_to_documented_operator_and_gemini_is_unavailable() -> None:
    resolved = {resolve_operator(agent_id) for agent_id in AGENT_REGISTRY}
    assert resolved <= OPERATORS
    assert {"CONTROL_TOWER", "CODEX", "CLAUDE", "NINEROUTER"} <= resolved
    assert "GEMINI" not in resolved


def test_assignment_package_never_uses_raw_dispatch_or_approval_sql() -> None:
    from pathlib import Path

    package = Path(__file__).parents[1] / "app" / "agent_assignment"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))
    assert "FROM dispatches" not in source
    assert "INTO dispatches" not in source
    assert "FROM approvals" not in source
    assert "INTO approvals" not in source
