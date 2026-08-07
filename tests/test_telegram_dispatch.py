"""Telegram dispatch command integration tests without network access."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from app.config import Settings
from app.dispatch.approvals import ApprovalService
from app.dispatch.service import DispatchService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.telegram_bot import (
    _approval_svc, _dispatch_svc, build_application, handle_approve,
    handle_canceldispatch, handle_dispatch, handle_dispatches,
    handle_dispatchstatus, handle_reject, handle_retrydispatch,
)


class FakeMessage:
    def __init__(self, text: str, message_id: int = 1) -> None:
        self.text = text
        self.message_id = message_id
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text: str) -> None:
        self.effective_message = FakeMessage(text)
        self.message = self.effective_message
        self.effective_user = SimpleNamespace(id=7)


class FakeContext:
    def __init__(self, dispatch: object | None, approvals: object | None) -> None:
        self.application = SimpleNamespace(bot_data={
            "settings": SimpleNamespace(telegram_allowed_user_id=7),
            "dispatch_svc": dispatch,
            "approval_svc": approvals,
        })


def run(handler, update: FakeUpdate, context: FakeContext) -> str:
    asyncio.run(handler(update, context))
    return update.effective_message.replies[-1]


def test_accessors_return_injected_services() -> None:
    dispatch, approvals = Mock(spec=DispatchService), Mock(spec=ApprovalService)
    context = FakeContext(dispatch, approvals)
    assert _dispatch_svc(context) is dispatch
    assert _approval_svc(context) is approvals


def test_missing_injection_is_sanitized() -> None:
    reply = run(handle_dispatch, FakeUpdate("/dispatch document_agent read_only"), FakeContext(None, None))
    assert "Dispatch creation failed" in reply
    assert "RuntimeError" not in reply
    assert "sqlite" not in reply.lower()


def test_dispatch_parses_separate_capability_and_payload_ref() -> None:
    dispatch = Mock(spec=DispatchService)
    dispatch.create_dispatch.return_value = SimpleNamespace(dispatch_id="dispatch-123456", status="pending")
    reply = run(handle_dispatch, FakeUpdate("/dispatch document_agent read_only opaque-ref"), FakeContext(dispatch, Mock()))
    request = dispatch.create_dispatch.call_args.args[0]
    assert request.capability == "read_only"
    assert request.payload_ref == "opaque-ref"
    assert "created" in reply


def test_dispatch_rejects_ambiguous_syntax() -> None:
    reply = run(handle_dispatch, FakeUpdate("/dispatch document_agent read_only ref extra"), FakeContext(Mock(), Mock()))
    assert reply.startswith("Usage:")


def test_dispatch_listing_and_status() -> None:
    dispatch = Mock(spec=DispatchService)
    dispatch.list_dispatches.return_value = [SimpleNamespace(dispatch_id="dispatch-123456", agent_id="document_agent", capability="read_only", status="pending")]
    assert "Recent dispatches" in run(handle_dispatches, FakeUpdate("/dispatches"), FakeContext(dispatch, Mock()))
    dispatch.get_dispatch.return_value = SimpleNamespace(dispatch_id="dispatch-123456", agent_id="document_agent", capability="read_only", status="succeeded")
    assert "Status: succeeded" in run(handle_dispatchstatus, FakeUpdate("/dispatchstatus dispatch-123456"), FakeContext(dispatch, Mock()))


def test_approve_reject_cancel_and_retry_handlers() -> None:
    dispatch, approvals = Mock(spec=DispatchService), Mock(spec=ApprovalService)
    approvals.approve.return_value = SimpleNamespace(dispatch_id="dispatch-123456")
    dispatch.dispatch.return_value = SimpleNamespace(status="succeeded")
    assert "Approval recorded" in run(handle_approve, FakeUpdate("/approve approval-123"), FakeContext(dispatch, approvals))
    assert dispatch.dispatch.called

    assert "Approval rejected" in run(handle_reject, FakeUpdate("/reject approval-123 not-needed"), FakeContext(dispatch, approvals))
    approvals.reject.assert_called_once()

    dispatch.cancel_dispatch.return_value = SimpleNamespace(dispatch_id="dispatch-123456")
    assert "cancelled" in run(handle_canceldispatch, FakeUpdate("/canceldispatch dispatch-123456"), FakeContext(dispatch, approvals)).lower()

    dispatch.retry_dispatch.return_value = SimpleNamespace(status="succeeded")
    assert "retry completed" in run(handle_retrydispatch, FakeUpdate("/retrydispatch dispatch-123456"), FakeContext(dispatch, approvals)).lower()


def test_command_registration_includes_each_dispatch_command_once(tmp_path) -> None:
    settings = Settings("token", 7, "test", tmp_path / "nova.sqlite3")
    memory = WorkspaceMemoryService(MemoryDatabase(tmp_path / "nova.sqlite3"))
    application = build_application(settings, memory, dispatch=Mock(spec=DispatchService), approvals=Mock(spec=ApprovalService))
    commands = [handler.commands for group in application.handlers.values() for handler in group if hasattr(handler, "commands")]
    flattened = [command for command_group in commands for command in command_group]
    for command in ("dispatch", "dispatches", "dispatchstatus", "approve", "reject", "canceldispatch", "retrydispatch"):
        assert flattened.count(command) == 1
    for command in ("start", "help", "project", "approvals"):
        assert command in flattened
