"""Telegram smoke tests for read-only Workspace intelligence commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from app.config import Settings
from app.google_workspace.calendar.dtos import PaginationResult
from app.google_workspace.gmail.dtos import MessageSearchResult
from app.telegram_bot import agenda_command, inbox_command
from app.workspace_intel import WorkspaceIntelService


@dataclass
class _FakeGmail:
    def search_messages(self, query: str, max_results: int = 20) -> MessageSearchResult:
        return MessageSearchResult((), False)


@dataclass
class _FakeCalendar:
    def list_today(self, now: object | None = None) -> PaginationResult:
        return PaginationResult()

    def detect_conflicts(self, window: object) -> tuple[object, ...]:
        return ()

    def build_meeting_brief(self, event: object) -> object:
        return event


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


def _context(service: WorkspaceIntelService) -> SimpleNamespace:
    settings = Settings("token", 7, "test", "/tmp/nova-workspace-intel.sqlite3")
    return SimpleNamespace(application=SimpleNamespace(bot_data={"settings": settings, "workspace_intel": service}))


def _update(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_message=_FakeMessage())


def test_inbox_command_renders_fact_inference_and_recommendation_for_authorized_user() -> None:
    update = _update(7)

    asyncio.run(inbox_command(update, _context(WorkspaceIntelService(_FakeGmail(), _FakeCalendar()))))

    assert update.effective_message.replies[0].startswith("NOVA — Prioritized Inbox")


def test_inbox_command_rejects_unauthorized_user() -> None:
    service = WorkspaceIntelService(_FakeGmail(), _FakeCalendar())
    denied = _update(8)

    asyncio.run(inbox_command(denied, _context(service)))

    assert denied.effective_message.replies == ["Akses ditolak. NOVA adalah AI Office pribadi."]


def test_agenda_command_renders_for_authorized_user_and_rejects_unauthorized_user() -> None:
    service = WorkspaceIntelService(_FakeGmail(), _FakeCalendar())
    allowed = _update(7)
    denied = _update(8)

    asyncio.run(agenda_command(allowed, _context(service)))
    asyncio.run(agenda_command(denied, _context(service)))

    assert allowed.effective_message.replies[0].startswith("NOVA — Upcoming Agenda")
    assert denied.effective_message.replies == ["Akses ditolak. NOVA adalah AI Office pribadi."]


def test_workspace_intel_commands_report_unavailable_when_not_wired() -> None:
    settings = Settings("token", 7, "test", "/tmp/nova-workspace-intel.sqlite3")
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"settings": settings, "workspace_intel": None}))
    inbox_update = _update(7)
    agenda_update = _update(7)

    asyncio.run(inbox_command(inbox_update, context))
    asyncio.run(agenda_command(agenda_update, context))

    assert inbox_update.effective_message.replies == ["Inbox intelligence is temporarily unavailable."]
    assert agenda_update.effective_message.replies == ["Agenda intelligence is temporarily unavailable."]
