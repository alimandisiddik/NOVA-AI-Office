from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from app.control_tower.models import WORK_ITEM_STATES
from app.control_tower.service import ControlTowerService
from app.memory.database import MemoryDatabase


@dataclass(frozen=True)
class AssignmentSummaryStub:
    assigned_agent_id: str
    operator_id: str


class AgentAssignmentsStub:
    def __init__(self, summary: AssignmentSummaryStub | None) -> None:
        self.summary = summary
        self.requested_item_ids: list[str] = []

    def get_active_assignment_summary(self, item_id: str) -> AssignmentSummaryStub | None:
        self.requested_item_ids.append(item_id)
        return self.summary


@pytest.fixture
def service(tmp_path) -> ControlTowerService:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    database.initialize()
    control_tower = ControlTowerService(database)
    control_tower.initialize()
    return control_tower


def test_owner_for_falls_back_to_recommended_route(service: ControlTowerService) -> None:
    item = service.capture_work("development", "Implement workflow", "user")

    assert service.owner_for(item) == "Development Agent"


def test_owner_for_uses_frozen_assignment_summary(service: ControlTowerService) -> None:
    assignments = AgentAssignmentsStub(AssignmentSummaryStub("coding_agent", "codex"))
    service.agent_assignments = assignments
    item = service.capture_work("development", "Implement workflow", "user")

    assert service.owner_for(item) == "coding_agent"
    assert assignments.requested_item_ids == [item.item_id]


@pytest.mark.parametrize("status", sorted(WORK_ITEM_STATES))
def test_next_action_for_every_work_item_state(service: ControlTowerService, status: str) -> None:
    item = service.capture_work("document", "Draft brief", "user", clarification_needs="Confirm audience")
    if status != "inbox":
        path = {
            "clarification_needed": ["clarification_needed"],
            "planned": ["planned"],
            "in_progress": ["in_progress"],
            "awaiting_approval": ["planned", "awaiting_approval"],
            "completed": ["in_progress", "completed"],
            "deferred": ["deferred"],
            "cancelled": ["cancelled"],
        }[status]
        for next_status in path:
            service.transition_work_item(item.item_id, next_status, "user", "workflow update")
        item = service.repository.get_work_item(item.item_id)

    expected = {
        "inbox": "Assign to Document Agent",
        "clarification_needed": "Resolve clarification: Confirm audience",
        "planned": "Assign to Document Agent",
        "in_progress": "In progress",
        "awaiting_approval": "Awaiting approval",
        "completed": "No action required",
        "deferred": "Deferred — reschedule or cancel",
        "cancelled": "No action required",
    }
    assert item is not None
    assert service.next_action_for(item) == expected[status]


def test_next_action_reports_unresolved_dependencies(service: ControlTowerService) -> None:
    dependency = service.capture_work("document", "Prerequisite", "user")
    item = service.capture_work("document", "Dependent work", "user", dependencies=[dependency.item_id])

    assert service.next_action_for(item) == "Blocked on dependencies"


def test_next_action_for_clarification_needed_without_stated_needs(service: ControlTowerService) -> None:
    item = service.capture_work("document", "Draft brief", "user")
    service.transition_work_item(item.item_id, "clarification_needed", "user", "needs review")
    item = service.repository.get_work_item(item.item_id)

    assert item is not None and item.clarification_needs is None
    assert service.next_action_for(item) == "Resolve clarification: Clarification required"


def test_owner_for_falls_back_to_unassigned_when_route_is_missing(service: ControlTowerService) -> None:
    item = service.capture_work("development", "Implement workflow", "user")
    routeless_item = replace(item, recommended_route=None)

    assert service.owner_for(routeless_item) == "Unassigned"


def test_owner_for_ignores_assignment_summary_when_service_returns_none(service: ControlTowerService) -> None:
    assignments = AgentAssignmentsStub(None)
    service.agent_assignments = assignments
    item = service.capture_work("development", "Implement workflow", "user")

    assert service.owner_for(item) == "Development Agent"
    assert assignments.requested_item_ids == [item.item_id]
