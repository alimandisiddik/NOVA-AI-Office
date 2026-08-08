from __future__ import annotations

import asyncio
import inspect
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from app.agent_assignment.service import AgentAssignmentService
from app.brief import models as brief_models
from app.brief import service as brief_service
from app.brief.service import ExecutiveBriefService
from app.config import Settings
from app.control_tower.service import ControlTowerService
from app.dispatch.approvals import ApprovalService
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.knowledge.service import KnowledgeService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.nightshift.service import NightShiftService
from app.telegram_bot import (
    _bounded_message, _executive_brief_lines, build_application, execbrief_handler, morning_handler,
)

FIXED_NOW = datetime(2026, 8, 8, 7, 0, tzinfo=datetime.now().astimezone().tzinfo)


def wire_services(tmp_path: Path) -> tuple[MemoryDatabase, WorkspaceMemoryService, ControlTowerService, AgentAssignmentService, KnowledgeService, ExecutiveBriefService]:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    database.initialize()
    memory = WorkspaceMemoryService(database)
    memory.initialize()
    approvals = ApprovalService(database, authorized_user_id=7)
    approvals.initialize()
    dispatch = DispatchService(database, registry=AgentRegistry(), approvals=approvals)
    dispatch.initialize()
    assignments = AgentAssignmentService(database, registry=AgentRegistry(), dispatch=dispatch, approvals=approvals)
    assignments.initialize()
    control_tower = ControlTowerService(database, agent_assignments=assignments)
    control_tower.initialize()
    knowledge = KnowledgeService(database, memory=memory, control_tower=control_tower)
    knowledge.initialize()
    return database, memory, control_tower, assignments, knowledge, ExecutiveBriefService(control_tower, knowledge=knowledge)


def test_empty_state_is_deterministic_and_provider_independent(tmp_path: Path) -> None:
    _, _, _, _, _, service = wire_services(tmp_path)

    first = service.generate_morning_brief(FIXED_NOW)
    second = service.generate_morning_brief(FIXED_NOW)

    assert first == second
    assert first.active_work == []
    assert first.knowledge_context == []
    assert first.night_shift_summary is None


def test_priority_assignment_fallback_blockers_and_knowledge_are_composed(tmp_path: Path) -> None:
    _, memory, tower, assignments, knowledge, service = wire_services(tmp_path)
    project = memory.create_project("Executive Office")
    blocker = tower.capture_work("document", "Approve outline", "user:7", urgency=1, importance=1)
    low = tower.capture_work("development", "Low priority", "user:7", urgency=0, importance=0)
    high = tower.capture_work(
        "development", "High priority", "user:7", project_id=project.id, urgency=5, importance=5, dependencies=[blocker.item_id]
    )
    proposed = assignments.propose_assignment(high.item_id, "draft_only", "coding_agent", "user:7")
    assignments.accept_assignment(proposed.assignment_id, "user:7")
    source = knowledge.create_source("Board notes", "note", "Board meeting August 2026", "user:7")
    knowledge.create_item(source.id, "Margin plan", "Use staged approval", "user:7", project_id=project.id, work_item_id=high.item_id)
    decision = tower.register_decision("Use staged approval", "Reduces risk", "Board review", "Executive", "user:7", project_id=project.id)
    tower.transition_work_item(low.item_id, "planned", "user:7", "planned")
    tower.transition_work_item(low.item_id, "awaiting_approval", "user:7", "requires approval")

    brief = service.generate_morning_brief(FIXED_NOW)

    assert [item.item_id for item in brief.active_priorities][:2] == [high.item_id, low.item_id]
    assert brief.owners[high.item_id] == "coding_agent"
    assert brief.next_actions[high.item_id] == "Blocked on dependencies"
    assert [item.item_id for item in brief.blockers] == [high.item_id]
    assert any(approval.source_item_id == low.item_id for approval in brief.approvals_pending)
    assert [(result.item.title, result.source.citation_text) for result in brief.knowledge_context] == [
        ("Margin plan", "Board meeting August 2026")
    ]
    assert brief.recent_decisions == [decision]


def test_terminal_assignment_is_not_an_active_owner_and_unassigned_falls_back(tmp_path: Path) -> None:
    _, _, tower, assignments, _, service = wire_services(tmp_path)
    active = tower.capture_work("development", "Assigned work", "user:7")
    unassigned = tower.capture_work("document", "Unassigned work", "user:7")
    proposed = assignments.propose_assignment(active.item_id, "draft_only", "coding_agent", "user:7")
    accepted = assignments.accept_assignment(proposed.assignment_id, "user:7")
    assignments.cancel_assignment(accepted.assignment_id, "user:7", "no longer needed")

    brief = service.generate_morning_brief(FIXED_NOW)

    assert brief.owners[active.item_id] == "Development Agent"
    assert brief.owners[unassigned.item_id] == "Document Agent"


def test_generation_is_read_only_for_canonical_tables(tmp_path: Path) -> None:
    """No canonical state is created or mutated by brief generation. The one
    exception is control_tower_audit_log: ControlTowerService.list_approvals()
    — the exact pre-existing public method docs/SPRINT_7C.md Sec.3 names as a
    composition input — has always recorded its own "approval_aggregation"
    audit row on every call, unchanged and pre-dating this sprint (the same
    row /morning's own morning_brief() already produces today). That is not a
    new side effect introduced by app/brief/, so it is asserted explicitly
    rather than folded into the "unchanged" set."""
    database, _, tower, _, _, service = wire_services(tmp_path)
    tower.capture_work("document", "Read only", "user:7")
    canonical_tables = [
        "control_tower_work_items", "control_tower_work_dependencies", "control_tower_decisions",
        "control_tower_approval_links", "agent_assignments", "agent_assignment_audit_log",
        "knowledge_sources", "knowledge_items", "knowledge_audit_log",
    ]
    with database.connection() as connection:
        before_canonical = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in canonical_tables}
        before_audit = connection.execute("SELECT COUNT(*) FROM control_tower_audit_log").fetchone()[0]

    service.generate_morning_brief(FIXED_NOW)

    with database.connection() as connection:
        after_canonical = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in canonical_tables}
        after_audit = connection.execute("SELECT COUNT(*) FROM control_tower_audit_log").fetchone()[0]
    assert after_canonical == before_canonical
    assert after_audit == before_audit + 1


def test_output_is_bounded_and_commands_are_registered_once(tmp_path: Path) -> None:
    _, memory, tower, assignments, knowledge, service = wire_services(tmp_path)
    for index in range(20):
        tower.capture_work("document", f"{index:02d} " + "x" * 190, "user:7", urgency=5, importance=5)
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    application = build_application(
        settings, memory, control_tower=tower, agent_assignments=assignments,
        knowledge=knowledge, executive_brief=service,
    )
    commands = [command for group in application.handlers.values() for handler in group for command in getattr(handler, "commands", set())]

    assert commands.count("morning") == 1
    assert commands.count("execbrief") == 1
    assert len(_bounded_message(_executive_brief_lines(service.generate_morning_brief(FIXED_NOW)))) <= 3500
    for command in {"capture", "today", "workitem", "assignments", "assignmentstatus", "knowledgesource", "knowledgeitem", "knowledgequery"}:
        assert commands.count(command) == 1


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


def test_execbrief_handler_requires_authorization_and_uses_executive_brief(tmp_path: Path) -> None:
    _, memory, tower, _, _, service = wire_services(tmp_path)
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    application = build_application(settings, memory, control_tower=tower, executive_brief=service)
    denied = SimpleNamespace(effective_user=SimpleNamespace(id=8), effective_message=_FakeMessage())
    allowed = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_message=_FakeMessage())
    context = SimpleNamespace(args=[], application=application)

    asyncio.run(execbrief_handler(denied, context))
    asyncio.run(execbrief_handler(allowed, context))

    assert denied.effective_message.replies == ["Akses ditolak. NOVA adalah AI Office pribadi."]
    assert allowed.effective_message.replies[0].startswith("NOVA — Morning Executive Brief")


def test_execbrief_handler_reports_unavailable_when_service_not_wired(tmp_path: Path) -> None:
    _, memory, tower, _, _, _ = wire_services(tmp_path)
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    application = build_application(settings, memory, control_tower=tower)
    allowed = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_message=_FakeMessage())
    context = SimpleNamespace(args=[], application=application)

    asyncio.run(execbrief_handler(allowed, context))

    assert allowed.effective_message.replies == ["Executive brief is temporarily unavailable."]


def test_morning_handler_is_unmodified_by_executive_brief_wiring(tmp_path: Path) -> None:
    """docs/SPRINT_7C.md Sec.2/Sec.14: /morning must keep its original, pre-7C
    output — the new brief is exposed only via the separate /execbrief command."""
    _, memory, tower, _, _, service = wire_services(tmp_path)
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    application = build_application(settings, memory, control_tower=tower, executive_brief=service)
    allowed = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_message=_FakeMessage())
    context = SimpleNamespace(args=[], application=application)

    asyncio.run(morning_handler(allowed, context))

    assert allowed.effective_message.replies[0].startswith("Morning brief:")


def test_integrated_morning_brief_preserves_night_shift_signal(tmp_path: Path) -> None:
    database, _, tower, _, knowledge, _ = wire_services(tmp_path)
    night_shift = NightShiftService(database)
    night_shift.initialize()
    night_shift.generate_morning_brief(FIXED_NOW)

    brief = ExecutiveBriefService(tower, night_shift=night_shift, knowledge=knowledge).generate_morning_brief(FIXED_NOW)

    assert any("Latest Night Shift morning brief is available." in line for line in _executive_brief_lines(brief))


def test_control_tower_files_not_modified_by_sprint_7c() -> None:
    """docs/SPRINT_7C.md Sec.13: 7C must never edit app/control_tower/**
    (7A-exclusive territory per docs/WAVE_6_SHARED_CONTRACTS.md Sec.4).
    Regression guard mirroring the existing Sprint 5G precedent
    (tests/test_nightshift_worker.py::test_dispatch_public_interface_unchanged_by_5g)."""
    result = subprocess.run(
        ["git", "diff", "--", "app/control_tower/repository.py", "app/control_tower/service.py"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_brief_module_has_no_network_or_provider_imports() -> None:
    """docs/SPRINT_7C.md Sec.10: no LLM/network call anywhere in the code
    path. Structural grep guard, mirroring Wave 3 Sec.8's precedent."""
    source = inspect.getsource(brief_service) + inspect.getsource(brief_models)
    forbidden_tokens = [
        "import requests", "import httpx", "import socket", "import urllib",
        "openai", "anthropic", "app.providers", "ProviderGatewayService",
    ]
    for token in forbidden_tokens:
        assert token not in source, f"forbidden token found in app/brief/: {token!r}"
