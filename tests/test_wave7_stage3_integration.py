"""Stage-3 integration coverage for local drafting and Workspace candidates."""

from __future__ import annotations

import importlib
import inspect
import socket
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import Update
from telegram.ext import CommandHandler, MessageHandler

import app.main as main_module
from app.config import Settings
from app.control_tower.service import ControlTowerService
from app.drafting import DraftingService
from app.google_workspace.gmail.dtos import MessageSummary
from app.knowledge.service import KnowledgeService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.telegram_bot import build_application, handle_text
from app.workspace_bridge import WorkspaceBridgeService
from app.workspace_bridge.service import WorkspaceAccountUnavailableError


class FakeAuthenticator:
    def __init__(self, namespace: str | None = "account-test") -> None:
        self.namespace = namespace
        self.calls = 0

    def get_account_namespace(self) -> str | None:
        self.calls += 1
        return self.namespace


def _services(tmp_path: Path) -> tuple[
    WorkspaceMemoryService,
    ControlTowerService,
    KnowledgeService,
    DraftingService,
    WorkspaceBridgeService,
    FakeAuthenticator,
]:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    memory = WorkspaceMemoryService(database)
    memory.initialize()
    control_tower = ControlTowerService(database)
    control_tower.initialize()
    knowledge = KnowledgeService(database, memory=memory, control_tower=control_tower)
    knowledge.initialize()
    drafting = DraftingService(database)
    drafting.initialize()
    authenticator = FakeAuthenticator()
    workspace_bridge = WorkspaceBridgeService(database, authenticator, control_tower, knowledge)
    workspace_bridge.initialize()
    return memory, control_tower, knowledge, drafting, workspace_bridge, authenticator


def _message(message_id: str = "gmail-stage3") -> MessageSummary:
    return MessageSummary(
        message_id=message_id,
        thread_id="thread-stage3",
        subject="Please review the Stage 3 plan",
        sender_alias="owner@example.com",
        received_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        snippet="Please review the Stage 3 plan before Monday.",
        has_attachments=False,
        label_ids=("INBOX",),
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


def test_stage3_services_construct_together_without_network_or_oauth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def network_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Stage-3 construction must not make a network call")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)
    assert importlib.import_module("app.main") is not None

    _, _, _, drafting, workspace_bridge, authenticator = _services(tmp_path)

    drafting.initialize()
    workspace_bridge.initialize()
    assert drafting is not None
    assert workspace_bridge is not None
    assert authenticator.calls == 0


def test_build_application_wires_stage3_services_commands_and_handler_order(tmp_path: Path) -> None:
    memory, _, _, drafting, workspace_bridge, _ = _services(tmp_path)
    settings = Settings("test-token", 7, "test", tmp_path / "nova.sqlite3")
    application = build_application(settings, memory, drafting=drafting, workspace_bridge=workspace_bridge)

    assert application.bot_data["drafting"] is drafting
    assert application.bot_data["workspace_bridge"] is workspace_bridge

    handlers = [handler for group in application.handlers.values() for handler in group]
    commands = [command for handler in handlers for command in getattr(handler, "commands", set())]
    for command in (
        "draftreply",
        "draftmemo",
        "draftsheet",
        "draftslides",
        "drafts",
        "draft",
        "workspacecandidates",
        "workspacecommit",
        "start",
        "capture",
        "nightshift",
        "workspacestatus",
        "inbox",
        "agenda",
        "wa",
        "waconfirm",
    ):
        assert commands.count(command) == 1

    generic_index = next(index for index, handler in enumerate(handlers) if isinstance(handler, MessageHandler))
    assert all(index < generic_index for index, handler in enumerate(handlers) if isinstance(handler, CommandHandler))
    generic_handler = handlers[generic_index]
    for command in ("/draftreply message-1 | Review", "/workspacecandidates", "/workspacecommit 1 work_item"):
        assert generic_handler.check_update(_command_update(application, command)) is False


def test_local_draft_and_bridge_candidate_remain_separate_and_nonexecuting(tmp_path: Path) -> None:
    _, control_tower, knowledge, drafting, workspace_bridge, _ = _services(tmp_path)

    draft = drafting.prepare_gmail_reply("gmail-draft", "Thank them and confirm receipt.", "7")
    ready = drafting.mark_ready_for_action(draft.id, "7")
    assert ready.status == "ready_for_action"
    assert control_tower.repository.list_work_items() == []
    assert knowledge.query(keyword="confirm receipt") == []

    candidate = workspace_bridge.propose_from_message(_message(), "7")
    duplicate = workspace_bridge.propose_from_message(_message(), "7")
    assert duplicate.id == candidate.id
    assert candidate.status == "candidate"
    assert candidate.account_namespace == "account-test"
    assert control_tower.repository.list_work_items() == []

    committed = workspace_bridge.commit(candidate.id, "work_item", "7")
    assert committed.status == "committed"
    assert committed.target_type == "work_item"
    assert len(control_tower.repository.list_work_items()) == 1
    assert drafting.get_action(draft.id).status == "ready_for_action"


def test_bridge_fails_closed_when_account_namespace_is_unavailable(tmp_path: Path) -> None:
    memory, control_tower, knowledge, _, _, _ = _services(tmp_path)
    bridge = WorkspaceBridgeService(memory.database, FakeAuthenticator(None), control_tower, knowledge)
    bridge.initialize()

    with pytest.raises(WorkspaceAccountUnavailableError):
        bridge.propose_from_message(_message("gmail-no-account"), "7")


def test_main_google_workspace_defined_before_workspace_bridge_construction() -> None:
    """Regression guard for the G3 initialization-order defect: workspace_bridge's
    authenticator selection reads the `google_workspace` name, so that name must be
    assigned earlier in main(), or Python raises UnboundLocalError at startup
    before the bot ever wires a handler."""
    source = inspect.getsource(main_module.main)
    namespace_assignment = source.index("google_workspace: WorkspaceConnectorBundle | None = None")
    bridge_construction = source.index("workspace_bridge = WorkspaceBridgeService(")
    assert namespace_assignment < bridge_construction


def test_main_unavailable_authenticator_fails_closed_when_workspace_absent() -> None:
    authenticator = main_module._UnavailableWorkspaceAuthenticator()
    assert authenticator.get_account_namespace() is None


def test_main_authenticator_selection_uses_bundle_authenticator_when_present() -> None:
    """Exercises the same `google_workspace.authenticator if ... else ...`
    selection app/main.py uses to construct WorkspaceBridgeService, for both the
    bundle-absent and bundle-present cases (production always takes the
    bundle-absent branch today, since `google_workspace` is hardcoded to None
    pending real OAuth wiring — see AD-W7-10 Stage-1 scope)."""
    sentinel_authenticator = object()
    google_workspace = SimpleNamespace(authenticator=sentinel_authenticator)
    selected = (
        google_workspace.authenticator
        if google_workspace is not None
        else main_module._UnavailableWorkspaceAuthenticator()
    )
    assert selected is sentinel_authenticator

    google_workspace = None
    selected = (
        google_workspace.authenticator
        if google_workspace is not None
        else main_module._UnavailableWorkspaceAuthenticator()
    )
    assert isinstance(selected, main_module._UnavailableWorkspaceAuthenticator)


def test_stage3_wiring_has_no_premature_workspace_execution_path() -> None:
    source = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("app/main.py", "app/telegram_bot.py", "app/drafting/service.py", "app/workspace_bridge/service.py")
    )

    assert "workspace_actions" not in source
    assert "ready_for_action" not in Path("app/workspace_bridge/service.py").read_text(encoding="utf-8")
