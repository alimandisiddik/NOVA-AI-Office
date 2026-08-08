"""Cross-sprint integration tests for the Wave 6 combined branch (7A + 7B + 7D).

Each sprint's own test suite already covers its behavior in isolation with
stubs/fakes. These tests prove the three merged sprints work together as one
runtime: real services wired to each other exactly as `app/main.py` wires
them, not mocked substitutes.
"""

from __future__ import annotations

from pathlib import Path

from app.agent_assignment.service import AgentAssignmentService
from app.config import Settings
from app.control_tower.service import ControlTowerService
from app.dispatch.approvals import ApprovalService
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.knowledge.service import KnowledgeService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.telegram_bot import build_application


def _wire_control_tower_and_assignments(tmp_path: Path) -> tuple[ControlTowerService, AgentAssignmentService]:
    """Build real (non-stub) ControlTowerService + AgentAssignmentService against one DB.

    Mirrors app/main.py's initialization order exactly: registry -> approvals ->
    dispatch -> agent_assignments -> control_tower(agent_assignments=...).
    """
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    database.initialize()

    registry = AgentRegistry()
    approvals = ApprovalService(database, authorized_user_id=7)
    approvals.initialize()
    dispatch = DispatchService(database, registry=registry, approvals=approvals)
    dispatch.initialize()
    agent_assignments = AgentAssignmentService(database, registry=registry, dispatch=dispatch, approvals=approvals)
    agent_assignments.initialize()

    control_tower = ControlTowerService(database, agent_assignments=agent_assignments)
    control_tower.initialize()
    return control_tower, agent_assignments


# --- A. Real AgentAssignmentService + ControlTowerService wiring ---------


def test_real_assignment_service_surfaces_active_owner_on_work_item(tmp_path: Path) -> None:
    control_tower, agent_assignments = _wire_control_tower_and_assignments(tmp_path)
    item = control_tower.capture_work("development", "Ship the feature", "user:7")

    proposed = agent_assignments.propose_assignment(item.item_id, "draft_only", "coding_agent", "user:7")
    agent_assignments.accept_assignment(proposed.assignment_id, "user:7")

    # owner_for() must reflect the real, non-terminal AgentAssignment, not the
    # coarse recommended_route category label.
    assert control_tower.owner_for(item) == "coding_agent"
    assert control_tower.next_action_for(item) == f"Assign to {item.recommended_route}"


def test_real_assignment_service_owner_reverts_to_route_once_assignment_completes(tmp_path: Path) -> None:
    control_tower, agent_assignments = _wire_control_tower_and_assignments(tmp_path)
    item = control_tower.capture_work("development", "Ship the feature", "user:7")

    proposed = agent_assignments.propose_assignment(item.item_id, "draft_only", "coding_agent", "user:7")
    accepted = agent_assignments.accept_assignment(proposed.assignment_id, "user:7")
    started = agent_assignments.start_execution(accepted.assignment_id, "user:7")
    agent_assignments.complete_assignment(started.assignment_id, "user:7")

    # Terminal assignment (completed) must never be surfaced as the active owner.
    assert control_tower.owner_for(item) == item.recommended_route


def test_real_assignment_service_reassignment_surfaces_new_agent_only(tmp_path: Path) -> None:
    control_tower, agent_assignments = _wire_control_tower_and_assignments(tmp_path)
    item = control_tower.capture_work("development", "Ship the feature", "user:7")

    proposed = agent_assignments.propose_assignment(item.item_id, "draft_only", "coding_agent", "user:7")
    accepted = agent_assignments.accept_assignment(proposed.assignment_id, "user:7")
    replacement = agent_assignments.reassign(accepted.assignment_id, "architecture_agent", "user:7")

    assert replacement.status == "proposed"
    assert control_tower.owner_for(item) == "architecture_agent"


# --- D. Unassigned fallback path (real service, no stub) -----------------


def test_real_assignment_service_no_assignment_falls_back_safely(tmp_path: Path) -> None:
    control_tower, agent_assignments = _wire_control_tower_and_assignments(tmp_path)
    item = control_tower.capture_work("development", "Untouched work item", "user:7")

    assert agent_assignments.get_active_assignment_summary(item.item_id) is None
    assert control_tower.owner_for(item) == item.recommended_route == "Development Agent"


def test_control_tower_without_injected_assignments_still_derives_owner(tmp_path: Path) -> None:
    """7A must not hard-depend on 7D: control_tower works with agent_assignments=None."""
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    database.initialize()
    control_tower = ControlTowerService(database)
    control_tower.initialize()
    item = control_tower.capture_work("document", "Draft memo", "user:7")

    assert control_tower.owner_for(item) == "Document Agent"


# --- B. build_application() carries every 7A/7B/7D service + handler -----


def _build_full_application(tmp_path: Path):
    control_tower, agent_assignments = _wire_control_tower_and_assignments(tmp_path)
    memory = WorkspaceMemoryService(MemoryDatabase(tmp_path / "nova.sqlite3"))
    memory.initialize()
    knowledge = KnowledgeService(
        MemoryDatabase(tmp_path / "nova.sqlite3"), memory=memory, control_tower=control_tower
    )
    knowledge.initialize()
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    application = build_application(
        settings,
        memory,
        control_tower=control_tower,
        agent_assignments=agent_assignments,
        knowledge=knowledge,
    )
    return application, control_tower, agent_assignments, knowledge


def test_build_application_carries_knowledge_and_assignment_services(tmp_path: Path) -> None:
    application, control_tower, agent_assignments, knowledge = _build_full_application(tmp_path)

    assert application.bot_data["control_tower"] is control_tower
    assert application.bot_data["agent_assignment_svc"] is agent_assignments
    assert application.bot_data["knowledge"] is knowledge


def test_build_application_registers_every_wave6_command_exactly_once(tmp_path: Path) -> None:
    application, *_ = _build_full_application(tmp_path)
    commands = [
        command
        for group in application.handlers.values()
        for handler in group
        for command in getattr(handler, "commands", set())
    ]

    expected_commands = {
        # 7A
        "capture", "today", "workitem",
        # 7B
        "knowledgesource", "knowledgeitem", "knowledgequery",
        # 7D
        "assignments", "assignmentstatus",
    }
    for command in expected_commands:
        assert commands.count(command) == 1, f"expected exactly one registration for /{command}"


def test_help_message_lists_every_wave6_command() -> None:
    from app.telegram_bot import HELP_MESSAGE

    for command in (
        "/capture", "/today", "/workitem",
        "/knowledgesource", "/knowledgeitem", "/knowledgequery",
        "/assignments", "/assignmentstatus",
    ):
        assert command in HELP_MESSAGE


def test_generic_text_handler_registered_after_all_command_handlers(tmp_path: Path) -> None:
    from telegram.ext import CommandHandler, MessageHandler

    application, *_ = _build_full_application(tmp_path)
    ordered_handlers = application.handlers[0]
    command_indices = [i for i, h in enumerate(ordered_handlers) if isinstance(h, CommandHandler)]
    text_fallback_indices = [
        i for i, h in enumerate(ordered_handlers) if isinstance(h, MessageHandler) and not isinstance(h, CommandHandler)
    ]

    assert command_indices, "no CommandHandlers registered"
    assert text_fallback_indices, "generic text fallback not registered"
    assert max(command_indices) < min(text_fallback_indices)


# --- C. Combined initialization against one temp SQLite DB is idempotent --


def test_combined_wave6_initialization_is_idempotent_against_one_database(tmp_path: Path) -> None:
    db_path = tmp_path / "nova.sqlite3"

    def initialize_all() -> None:
        memory = WorkspaceMemoryService(MemoryDatabase(db_path))
        memory.initialize()
        registry = AgentRegistry()
        approvals = ApprovalService(MemoryDatabase(db_path), authorized_user_id=7)
        approvals.initialize()
        dispatch = DispatchService(MemoryDatabase(db_path), registry=registry, approvals=approvals)
        dispatch.initialize()
        agent_assignments = AgentAssignmentService(
            MemoryDatabase(db_path), registry=registry, dispatch=dispatch, approvals=approvals
        )
        agent_assignments.initialize()
        control_tower = ControlTowerService(MemoryDatabase(db_path), agent_assignments=agent_assignments)
        control_tower.initialize()
        knowledge = KnowledgeService(MemoryDatabase(db_path), memory=memory, control_tower=control_tower)
        knowledge.initialize()

    # First pass creates every table; second pass against the same file must
    # succeed cleanly (all schema.py modules use CREATE TABLE IF NOT EXISTS).
    initialize_all()
    initialize_all()


# --- Cross-sprint: Knowledge never introduces AgentAssignment/ControlTower ownership ---


def test_knowledge_module_never_writes_control_tower_or_assignment_tables() -> None:
    import pathlib

    knowledge_dir = pathlib.Path(__file__).resolve().parent.parent / "app" / "knowledge"
    forbidden_tables = {"agent_assignments", "control_tower_work_items"}
    for path in knowledge_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for table in forbidden_tables:
            assert table not in source, f"{path} unexpectedly references {table}"
