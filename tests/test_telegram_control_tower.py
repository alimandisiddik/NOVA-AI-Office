"""Handler-level tests for Executive Control Tower Telegram ownership."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.control_tower.service import ControlTowerService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.telegram_bot import (
    UNAUTHORIZED_MESSAGE,
    approvals_handler,
    build_application,
    capture_handler,
    decision_command,
    morning_handler,
    shutdown_handler,
    today_handler,
    workitem_handler,
)


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id: int = 7) -> None:
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_message = FakeMessage()


def services(tmp_path: Path) -> tuple[WorkspaceMemoryService, ControlTowerService]:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    memory = WorkspaceMemoryService(database)
    memory.initialize()
    tower = ControlTowerService(database)
    tower.initialize()
    return memory, tower


def context(memory: WorkspaceMemoryService, tower: ControlTowerService, args: list[str]) -> SimpleNamespace:
    settings = Settings("test-token", 7, "test", Path("/tmp/nova-test.db"))
    return SimpleNamespace(args=args, application=SimpleNamespace(bot_data={
        "settings": settings, "memory": memory, "control_tower": tower,
    }))


def run(handler, update: FakeUpdate, ctx: SimpleNamespace) -> str:
    asyncio.run(handler(update, ctx))
    return update.effective_message.replies[-1]


def test_all_control_tower_handlers_use_injected_service(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)
    assert "Captured:" in run(capture_handler, FakeUpdate(), context(memory, tower, ["document", "Draft", "brief"]))
    assert "Today priorities:" in run(today_handler, FakeUpdate(), context(memory, tower, []))
    item_id = tower.get_today_priorities()[0].item_id
    assert "Work item:" in run(workitem_handler, FakeUpdate(), context(memory, tower, [item_id]))
    assert "Control Tower decision recorded:" in run(decision_command, FakeUpdate(), context(memory, tower, ["Approve", "briefing", "format"]))
    assert "Approval inbox:" in run(approvals_handler, FakeUpdate(), context(memory, tower, []))
    assert "Evening shutdown:" in run(shutdown_handler, FakeUpdate(), context(memory, tower, []))
    assert "Morning brief:" in run(morning_handler, FakeUpdate(), context(memory, tower, []))


def test_legacy_and_control_tower_decision_paths_remain_reachable(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)
    memory.create_project("NOVA")
    assert "Decision stored for NOVA." in run(decision_command, FakeUpdate(), context(memory, tower, ["NOVA", "|", "Use", "SQLite"]))
    assert "Control Tower decision recorded:" in run(decision_command, FakeUpdate(), context(memory, tower, ["Adopt", "explicit", "injection"]))


def test_control_tower_handler_errors_do_not_leak_internal_details(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)

    def integrity_error(*args, **kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: local/path secret")

    tower.capture_work = integrity_error  # type: ignore[method-assign]
    reply = run(capture_handler, FakeUpdate(), context(memory, tower, ["document", "secret"]))
    assert "UNIQUE" not in reply and "local/path" not in reply and "secret" not in reply

    def name_error(*args, **kwargs):
        raise NameError("control_tower_service is not defined")

    tower.get_today_priorities = name_error  # type: ignore[method-assign]
    reply = run(today_handler, FakeUpdate(), context(memory, tower, []))
    assert "NameError" not in reply and "control_tower_service" not in reply


def test_build_application_registers_each_control_tower_command_once(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    application = build_application(settings, memory, control_tower=tower)
    commands = [command for group in application.handlers.values() for handler in group for command in getattr(handler, "commands", set())]
    for command in {"capture", "today", "workitem", "decision", "approvals", "shutdown", "morning"}:
        assert commands.count(command) == 1
    assert application.bot_data["control_tower"] is tower

def test_capture_with_existing_project_links_work_item(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)
    project = memory.create_project("Executive Office")

    reply = run(capture_handler, FakeUpdate(), context(memory, tower, ["document", "Draft", "brief", "|", "Executive", "Office"]))

    assert "Captured:" in reply
    item = tower.get_today_priorities()[0]
    assert item.project_id == project.id

def test_capture_without_project_preserves_unlinked_behavior(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)

    reply = run(capture_handler, FakeUpdate(), context(memory, tower, ["document", "Draft", "brief"]))

    assert "Captured:" in reply
    assert tower.get_today_priorities()[0].project_id is None

def test_capture_with_unknown_project_rejects_without_partial_write(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)

    reply = run(capture_handler, FakeUpdate(), context(memory, tower, ["document", "Draft", "brief", "|", "Missing"]))

    assert "Capture failed" in reply
    assert tower.get_today_priorities() == []

def test_today_and_workitem_show_workflow_details(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)
    item = tower.capture_work("document", "Draft brief", "user")

    today = run(today_handler, FakeUpdate(), context(memory, tower, []))
    detail = run(workitem_handler, FakeUpdate(), context(memory, tower, [item.item_id]))

    assert "Status: inbox" in today
    assert "Owner: Document Agent" in today
    assert "Next action: Assign to Document Agent" in today
    assert "Status: inbox" in detail
    assert "Owner: Document Agent" in detail
    assert "Next action: Assign to Document Agent" in detail
    assert "Dependencies: None" in detail
    assert "Decisions: None recorded." in detail

def test_workitem_shows_decisions_linked_to_the_items_project(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)
    project = memory.create_project("Executive Office")
    item = tower.capture_work("document", "Draft brief", "user", project_id=project.id)
    tower.register_decision("Ship the brief", "Deadline driven", "Low risk", "COO", "user", project_id=project.id)

    detail = run(workitem_handler, FakeUpdate(), context(memory, tower, [item.item_id]))

    assert "Decisions: Ship the brief" in detail

def test_workitem_captured_id_is_directly_usable(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)
    reply = run(capture_handler, FakeUpdate(), context(memory, tower, ["document", "Draft", "brief"]))
    item_id = reply.split("(")[1].split(")")[0]

    detail = run(workitem_handler, FakeUpdate(), context(memory, tower, [item_id]))

    assert "Work item lookup failed" not in detail
    assert "Work item: Draft brief" in detail

def test_workitem_not_found_is_sanitized(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)

    reply = run(workitem_handler, FakeUpdate(), context(memory, tower, ["missing-item"]))

    assert "Work item lookup failed" in reply
    assert "missing-item" not in reply

def test_workitem_requires_authorization(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)
    item = tower.capture_work("document", "Draft brief", "user")

    reply = run(workitem_handler, FakeUpdate(user_id=99), context(memory, tower, [item.item_id]))

    assert reply == UNAUTHORIZED_MESSAGE

def test_capture_with_sensitive_project_name_is_rejected(tmp_path: Path) -> None:
    memory, tower = services(tmp_path)

    reply = run(capture_handler, FakeUpdate(), context(memory, tower, ["document", "Draft", "brief", "|", "API_KEY=xyz123"]))

    assert "Capture failed" in reply
    assert tower.get_today_priorities() == []
