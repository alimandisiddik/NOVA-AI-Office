"""Stage-1 integration coverage for Workspace, manual intake, and conversation control."""

from __future__ import annotations

import asyncio
import importlib
import socket
from pathlib import Path
from types import SimpleNamespace

from telegram import Update
from telegram.ext import CommandHandler, MessageHandler

from app.config import Settings
from app.conversation import Choice, ConversationService
from app.google_workspace.auth import GoogleAuthenticator, SecureFileTokenStorage
from app.google_workspace.bundle import WorkspaceConnectorBundle
from app.google_workspace.calendar.audit import InMemoryCalendarAuditSink
from app.google_workspace.calendar.service import CalendarService
from app.google_workspace.docs.service import DocsService
from app.google_workspace.drive.models import AllowlistedFolder
from app.google_workspace.drive.service import DriveReadService
from app.google_workspace.factory import GoogleClientFactory
from app.google_workspace.gmail.service import GmailService
from app.google_workspace.sheets.service import SheetsService
from app.google_workspace.slides.service import SlidesService
from app.google_workspace.telegram import workspacestatus_command
from app.intake import IntakeService
from app.intake.telegram import wa_command, waconfirm_command
from app.knowledge.service import KnowledgeService
from app.control_tower.service import ControlTowerService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.telegram_bot import build_application, handle_text


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id: int = 7, text: str = "", update_id: int = 1) -> None:
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_message = FakeMessage(text)
        self.update_id = update_id


def _workspace_bundle(tmp_path: Path) -> WorkspaceConnectorBundle:
    client_secrets = tmp_path / "google-client.json"
    client_secrets.write_text('{"installed": {"client_id": "test-client"}}', encoding="utf-8")
    authenticator = GoogleAuthenticator(
        client_secrets,
        SecureFileTokenStorage(tmp_path / "google-token.json"),
        scopes=(
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/documents.readonly",
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/presentations.readonly",
        ),
    )
    factory = GoogleClientFactory(authenticator)
    return WorkspaceConnectorBundle(
        authenticator=authenticator,
        factory=factory,
        gmail=GmailService(factory),
        calendar=CalendarService(factory, InMemoryCalendarAuditSink()),
        drive=DriveReadService(
            factory,
            [AllowlistedFolder(folder_id="folder-id", alias="workspace")],
            tmp_path / "workspace-exports",
        ),
        docs=DocsService(factory),
        sheets=SheetsService(factory),
        slides=SlidesService(factory),
    )


def _services(tmp_path: Path) -> tuple[
    WorkspaceMemoryService, ControlTowerService, KnowledgeService, IntakeService, ConversationService
]:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    memory = WorkspaceMemoryService(database)
    memory.initialize()
    control_tower = ControlTowerService(database)
    control_tower.initialize()
    knowledge = KnowledgeService(database, memory=memory, control_tower=control_tower)
    knowledge.initialize()
    intake = IntakeService(database, memory=memory, control_tower=control_tower, knowledge=knowledge)
    intake.initialize()
    conversation = ConversationService(database)
    conversation.initialize()
    return memory, control_tower, knowledge, intake, conversation


def _context(bot_data: dict[str, object], args: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        args=args,
        bot_data=bot_data,
        application=SimpleNamespace(bot_data=bot_data),
    )


def _command_update(application: object, command: str) -> Update:
    return Update.de_json(
        {
            "update_id": 100,
            "message": {
                "message_id": 1,
                "date": 0,
                "chat": {"id": 7, "type": "private"},
                "from": {"id": 7, "is_bot": False, "first_name": "Test"},
                "text": command,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(command)}],
            },
        },
        application.bot,  # type: ignore[arg-type]
    )


def test_stage1_services_construct_together_without_network_or_oauth(tmp_path: Path, monkeypatch) -> None:
    def network_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Stage-1 construction must not make a network call")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    assert importlib.import_module("app.main") is not None

    bundle = _workspace_bundle(tmp_path)
    _, _, _, intake, conversation = _services(tmp_path)

    assert bundle.authenticator.get_connection_status()["is_connected"] is False
    assert isinstance(intake, IntakeService)
    assert isinstance(conversation, ConversationService)


def test_build_application_wires_stage1_services_commands_and_order(tmp_path: Path) -> None:
    memory, _, _, intake, conversation = _services(tmp_path)
    bundle = _workspace_bundle(tmp_path)
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    application = build_application(
        settings,
        memory,
        google_workspace=bundle,
        intake=intake,
        conversation=conversation,
    )

    assert application.bot_data["google_workspace"] is bundle
    assert application.bot_data["intake"] is intake
    assert application.bot_data["conversation"] is conversation

    handlers = [handler for group in application.handlers.values() for handler in group]
    commands = [command for handler in handlers for command in getattr(handler, "commands", set())]
    for command in ("workspacestatus", "wa", "waconfirm", "start", "capture", "nightshift"):
        assert commands.count(command) == 1

    generic_index = next(index for index, handler in enumerate(handlers) if isinstance(handler, MessageHandler))
    assert all(
        index < generic_index
        for index, handler in enumerate(handlers)
        if isinstance(handler, CommandHandler)
    )
    generic_handler = handlers[generic_index]
    for command in ("/wa message", "/waconfirm 1 work_item", "/workspacestatus"):
        assert generic_handler.check_update(_command_update(application, command)) is False


def test_stage1_handlers_preserve_safe_workspace_intake_and_conversation_behavior(tmp_path: Path) -> None:
    memory, control_tower, knowledge, intake, conversation = _services(tmp_path)
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    bot_data = {
        "settings": settings,
        "memory": memory,
        "control_tower": control_tower,
        "knowledge": knowledge,
        "intake": intake,
        "conversation": conversation,
    }

    workspace_update = FakeUpdate()
    asyncio.run(workspacestatus_command(workspace_update, _context(bot_data, [])))
    assert "gmail: not_configured" in workspace_update.effective_message.replies[-1]

    intake_update = FakeUpdate(update_id=44)
    asyncio.run(wa_command(intake_update, _context(bot_data, ["Task:", "Prepare", "brief"])))
    record = intake.repository.get(1)
    assert record.status == "pending_review"
    assert control_tower.repository.list_work_items() == []

    confirmation_update = FakeUpdate()
    asyncio.run(waconfirm_command(confirmation_update, _context(bot_data, ["1", "work_item"])))
    assert intake.repository.get(1).status == "confirmed"
    assert len(control_tower.repository.list_work_items()) == 1
    assert "Confirmed external message #1" in confirmation_update.effective_message.replies[-1]

    conversation.ask(
        "chat-low",
        7,
        "/test",
        "Confirm safe operation",
        [Choice(1, "Continue", "low", "continue-token")],
    )
    low_update = FakeUpdate(text="oke")
    asyncio.run(handle_text(low_update, _context(bot_data, [])))
    assert low_update.effective_message.replies == ["Selected: Continue"]

    conversation.ask(
        "chat-high",
        7,
        "/test",
        "Confirm high-risk operation",
        [Choice(1, "Publish report", "high", "publish-token")],
    )
    high_update = FakeUpdate(text="oke")
    asyncio.run(handle_text(high_update, _context(bot_data, [])))
    assert "exact numbered choice" in high_update.effective_message.replies[-1]

    resolved = conversation.try_resolve_pending(7, "1")
    assert resolved is not None and resolved.action_token == "publish-token"
    assert conversation.try_resolve_pending(7, "1") is None
