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
    approvals_handler,
    build_application,
    capture_handler,
    decision_command,
    morning_handler,
    shutdown_handler,
    today_handler,
)


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self) -> None:
        self.effective_user = SimpleNamespace(id=7)
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
    for command in {"capture", "today", "decision", "approvals", "shutdown", "morning"}:
        assert commands.count(command) == 1
    assert application.bot_data["control_tower"] is tower
