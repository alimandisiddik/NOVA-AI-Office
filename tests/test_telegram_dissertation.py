"""Read-only Telegram surface tests for Sprint 6A Dissertation Workspace."""
from __future__ import annotations
import asyncio
from pathlib import Path
from types import SimpleNamespace
from app.config import Settings
from app.dissertation.service import DissertationService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.telegram_bot import _dissertation, build_application, dissertation_handler

class FakeMessage:
    def __init__(self) -> None: self.replies: list[str] = []
    async def reply_text(self, text: str) -> None: self.replies.append(text)
class FakeUpdate:
    def __init__(self) -> None:
        self.effective_user = SimpleNamespace(id=7)
        self.effective_message = FakeMessage()

def context(service, args):
    settings = Settings("token", 7, "test", Path("/tmp/nova-test.db"))
    return SimpleNamespace(args=args, application=SimpleNamespace(bot_data={"settings": settings, "dissertation": service}))

def run(args, service):
    update = FakeUpdate()
    asyncio.run(dissertation_handler(update, context(service, args)))
    return update.effective_message.replies[-1]

def dissertation(tmp_path: Path) -> DissertationService:
    service = DissertationService(MemoryDatabase(tmp_path / "dissertation.sqlite3"))
    service.initialize()
    service.initialize_workspace("Thesis", "PhD", "owner")
    service.create_chapter("Introduction", 1)
    return service

def test_all_read_only_dissertation_views_are_reachable(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    assert "Dissertation: Thesis" in run([], service)
    assert "Chapter 1:" in run(["chapter", "1"], service)
    assert "Open dissertation gaps:" in run(["gaps"], service)
    assert "Next academic action:" in run(["next"], service)
    assert "Academic tasks:" in run(["tasks"], service)
    assert "Evidence for chapter 1:" in run(["evidence", "1"], service)
    assert "Dissertation sources:" in run(["sources"], service)
    assert "Dissertation decisions:" in run(["decisions"], service)

def test_overview_and_chapter_views_render_progress(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    chapter = service.list_chapters()[0]
    service.update_chapter_status(chapter.id, "revised")  # weight 75

    overview_reply = run([], service)
    assert "Progress: 75%" in overview_reply

    chapter_reply = run(["chapter", "1"], service)
    assert "Progress: 75%" in chapter_reply


def test_accessor_and_missing_service_degrade_safely(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    assert _dissertation(context(service, [])) is service
    assert "temporarily unavailable" in run([], service=None)

def test_build_application_registers_dissertation_once(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    memory = WorkspaceMemoryService(database)
    memory.initialize()
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    service = dissertation(tmp_path)
    application = build_application(settings, memory, dissertation=service) # type: ignore[arg-type]
    commands = [command for group in application.handlers.values() for handler in group for command in getattr(handler, "commands", set())]
    assert commands.count("dissertation") == 1
    assert application.bot_data["dissertation"] is service
