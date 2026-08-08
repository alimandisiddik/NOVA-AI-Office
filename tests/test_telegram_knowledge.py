"""Telegram command coverage for Knowledge Operations."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.knowledge.service import KnowledgeService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.telegram_bot import (
    UNAUTHORIZED_MESSAGE,
    build_application,
    knowledgeitem_handler,
    knowledgequery_handler,
    knowledgesource_handler,
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


def context(service: KnowledgeService, args: list[str]):
    settings = Settings("token", 7, "test", Path("/tmp/nova-test.db"))
    return SimpleNamespace(args=args, application=SimpleNamespace(bot_data={"settings": settings, "knowledge": service}))


def knowledge(tmp_path: Path) -> KnowledgeService:
    database = MemoryDatabase(tmp_path / "knowledge.sqlite3")
    database.initialize()
    service = KnowledgeService(database)
    service.initialize()
    return service


def run(handler, args: list[str], service: KnowledgeService, user_id: int = 7) -> str:
    update = FakeUpdate(user_id)
    asyncio.run(handler(update, context(service, args)))
    return update.effective_message.replies[-1]


def test_knowledge_commands_round_trip_with_citation_provenance(tmp_path: Path) -> None:
    service = knowledge(tmp_path)
    source_reply = run(knowledgesource_handler, "Board notes | note | Board meeting, August 2026 | file-123".split(), service)
    item_reply = run(knowledgeitem_handler, "1 | Margin plan | Improve service margin | finance | HIGH".split(), service)
    query_reply = run(knowledgequery_handler, ["margin"], service)

    assert "Knowledge source created: #1 Board notes (note)" == source_reply
    assert "Knowledge item created: #1 from source #1 (confidence: HIGH)" == item_reply
    assert "Margin plan" in query_reply
    assert "Citation: Board meeting, August 2026" in query_reply


def test_commands_report_usage_for_missing_or_malformed_pipes(tmp_path: Path) -> None:
    service = knowledge(tmp_path)

    assert "Usage: /knowledgesource" in run(knowledgesource_handler, [], service)
    assert "Usage: /knowledgeitem" in run(knowledgeitem_handler, ["1", "|", "Title"], service)
    assert "Usage: /knowledgequery" in run(knowledgequery_handler, [], service)


def test_commands_reject_unauthorized_user(tmp_path: Path) -> None:
    service = knowledge(tmp_path)

    assert run(knowledgesource_handler, "Title | manual | Citation".split(), service, user_id=999) == UNAUTHORIZED_MESSAGE


def test_build_application_registers_knowledge_commands_once(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    memory = WorkspaceMemoryService(database)
    memory.initialize()
    service = knowledge(tmp_path)
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")

    application = build_application(settings, memory, knowledge=service)  # type: ignore[arg-type]
    commands = [command for group in application.handlers.values() for handler in group for command in getattr(handler, "commands", set())]

    assert commands.count("knowledgesource") == 1
    assert commands.count("knowledgeitem") == 1
    assert commands.count("knowledgequery") == 1
    assert application.bot_data["knowledge"] is service
