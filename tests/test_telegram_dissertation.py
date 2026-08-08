"""Telegram surface tests for the Dissertation Workspace (Sprint 6A read views, Sprint 6B writes)."""
from __future__ import annotations
import asyncio
from pathlib import Path
from types import SimpleNamespace
from app.config import Settings
from app.dissertation.service import DissertationService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.telegram_bot import (
    UNAUTHORIZED_MESSAGE,
    _dissertation,
    build_application,
    dissertation_handler,
)

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


def test_sources_and_evidence_views_expose_ids_for_write_reference(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    chapter = service.list_chapters()[0]
    source = service.create_source("Book A", "book", "Citation", "owner", chapter_id=chapter.id)
    evidence = service.create_evidence(source.id, "Summary", "owner", chapter_id=chapter.id)

    sources_reply = run(["sources"], service)
    assert f"#{source.id} Book A" in sources_reply

    evidence_reply = run(["evidence", "1"], service)
    assert f"#{evidence.id} Summary (source #{source.id})" in evidence_reply


def test_addsource_creates_source_and_links_chapter(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    reply = run("addsource 1 | Book Title | book | Some citation text | http://example.com/loc".split(), service)
    assert "Source created: #1 Book Title (book, unread)" in reply

    sources = service.list_sources(chapter_id=service.list_chapters()[0].id)
    assert len(sources) == 1
    assert sources[0].title == "Book Title"
    assert sources[0].locator == "http://example.com/loc"


def test_addsource_without_chapter_link(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    reply = run("addsource - | Solo Book | book | Citation".split(), service)
    assert "Source created: #1 Solo Book (book, unread)" in reply
    assert service.list_sources() != []
    assert service.list_sources(chapter_id=service.list_chapters()[0].id) == []


def test_addsource_rejects_invalid_source_type(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    reply = run("addsource - | Bad | notatype | Citation".split(), service)
    assert "Invalid source type" in reply
    assert service.list_sources() == []


def test_addsource_rejects_sensitive_content(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    reply = run("addsource - | Title | book | api_key=secret123".split(), service)
    assert "Sensitive values cannot be stored" in reply
    assert service.list_sources() == []


def test_addsource_reports_usage_on_missing_fields(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    reply = run(["addsource"], service)
    assert "Usage: /dissertation addsource" in reply


def test_addevidence_creates_evidence_for_existing_source(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    source = service.create_source("S1", "book", "Citation", "owner")

    reply = run(f"addevidence {source.id} | 1 | Key finding summary".split(), service)
    assert f"Evidence created: #1 for source #{source.id}" in reply

    evidence = service.list_evidence(chapter_id=service.list_chapters()[0].id)
    assert len(evidence) == 1
    assert evidence[0].summary == "Key finding summary"


def test_addevidence_defaults_confidence_to_medium_when_omitted(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    source = service.create_source("S1", "book", "Citation", "owner")

    reply = run(f"addevidence {source.id} | 1 | Key finding summary".split(), service)
    assert "(confidence: MEDIUM)" in reply

    evidence = service.list_evidence(chapter_id=service.list_chapters()[0].id)
    assert evidence[0].confidence == "MEDIUM"


def test_addevidence_accepts_explicit_confidence_value(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    source = service.create_source("S1", "book", "Citation", "owner")

    reply = run(f"addevidence {source.id} | 1 | Key finding summary | p. 12 | HIGH".split(), service)
    assert "(confidence: HIGH)" in reply

    evidence = service.list_evidence(chapter_id=service.list_chapters()[0].id)
    assert evidence[0].confidence == "HIGH"
    assert evidence[0].locator_detail == "p. 12"


def test_addevidence_confidence_is_case_insensitive(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    source = service.create_source("S1", "book", "Citation", "owner")

    reply = run(f"addevidence {source.id} | 1 | Key finding summary | p. 12 | low".split(), service)
    assert "(confidence: LOW)" in reply


def test_addevidence_rejects_invalid_confidence_value(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    source = service.create_source("S1", "book", "Citation", "owner")

    reply = run(f"addevidence {source.id} | 1 | Key finding summary | p. 12 | EXTREME".split(), service)
    assert "Invalid evidence confidence" in reply
    assert service.list_evidence() == []


def test_addevidence_rejects_unknown_source(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    reply = run("addevidence 999 | - | Summary".split(), service)
    assert "Source not found" in reply


def test_addevidence_rejects_unknown_chapter(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    source = service.create_source("S1", "book", "Citation", "owner")
    reply = run(f"addevidence {source.id} | 99 | Summary".split(), service)
    assert "Chapter not found" in reply


def test_addevidence_rejects_sensitive_content(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    source = service.create_source("S1", "book", "Citation", "owner")
    reply = run(f"addevidence {source.id} | - | password=secret".split(), service)
    assert "Sensitive values cannot be stored" in reply


def test_addevidence_reports_usage_on_missing_fields(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    reply = run(["addevidence"], service)
    assert "Usage: /dissertation addevidence" in reply


def test_addsource_and_addevidence_reject_unauthorized_user(tmp_path: Path) -> None:
    service = dissertation(tmp_path)
    update = FakeUpdate()
    update.effective_user = SimpleNamespace(id=999)
    asyncio.run(
        dissertation_handler(
            update,
            context(service, "addsource - | Title | book | Citation".split()),
        )
    )
    assert update.effective_message.replies[-1] == UNAUTHORIZED_MESSAGE
    assert service.list_sources() == []
