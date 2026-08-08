from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.dispatch.approvals import ApprovalService
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.drafting.service import DraftingService
from app.memory.database import MemoryDatabase
from app.telegram_bot import build_application
from app.workspace_actions import WorkspaceActionService
from app.workspace_actions.models import WorkspaceAction
from app.workspace_actions.telegram import createdoc_command, docstatus_command, status_text


class Writer:
    def __init__(self): self.calls = 0
    def create_private_document(self, title, body):
        del title, body; self.calls += 1
        return type("Created", (), {"document_id": "doc"})()


class Message:
    def __init__(self, message_id=4): self.message_id, self.replies = message_id, []
    async def reply_text(self, value): self.replies.append(value)


def _context(tmp_path):
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    drafting = DraftingService(database); drafting.initialize()
    approvals = ApprovalService(database, authorized_user_id=7); approvals.initialize()
    dispatch = DispatchService(database, registry=AgentRegistry(), approvals=approvals); dispatch.initialize()
    writer = Writer()
    actions = WorkspaceActionService(database, drafting=drafting, dispatch=dispatch, approvals=approvals, docs_writer=writer); actions.initialize()
    context = SimpleNamespace(args=[], application=SimpleNamespace(bot_data={"settings": SimpleNamespace(telegram_allowed_user_id=7), "workspace_actions": actions}))
    return drafting, approvals, actions, writer, context


def _update(user_id=7, message_id=4):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_message=Message(message_id), effective_chat=SimpleNamespace(id=9))


def test_createdoc_requires_ready_memo_and_emits_exact_approval_copy(tmp_path):
    drafting, _approvals, actions, _writer, context = _context(tmp_path)
    prepared = drafting.mark_ready_for_action(drafting.prepare_docs_memo("Memo", "Body", "user:7").id, "user:7")
    context.args = [str(prepared.id)]
    update = _update()
    asyncio.run(createdoc_command(update, context))
    assert update.effective_message.replies == [f"1. Create private Google Doc #1\nApprove with /approve {actions.get_action(1).approval_id}."]
    duplicate = _update(message_id=4)
    asyncio.run(createdoc_command(duplicate, context))
    assert actions.get_action(1).id == 1


def test_docstatus_surfaces_reconciliation_requirement(tmp_path):
    _drafting, _approvals, actions, _writer, context = _context(tmp_path)
    with actions.database.connection() as connection:
        connection.execute("INSERT INTO workspace_actions (action_type, prepared_action_id, payload_schema_version, payload_fingerprint, idempotency_key, correlation_id, status, version, requested_at, updated_at) VALUES ('create_docs_file', 1, 1, 'hash', 'status', 'corr', 'outcome_unknown', 1, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')")
    context.args = ["1"]
    update = _update()
    asyncio.run(docstatus_command(update, context))
    reply = update.effective_message.replies[-1]
    # G4 fix: substance required by docs/SPRINT_8E.md §5 — all three must be present.
    assert "COULD NOT BE CONFIRMED" in reply
    assert "DO NOT RETRY AUTOMATICALLY" in reply
    assert "RECONCILIATION REQUIRED" in reply


def test_status_text_outcome_unknown_states_no_retry_and_reconciliation():
    action = WorkspaceAction(
        id=1, action_type="create_docs_file", prepared_action_id=1, dispatch_id="d", approval_id="a",
        payload_schema_version=1, payload_fingerprint="hash", account_namespace=None,
        idempotency_key="key", correlation_id="corr", requested_chat_id=None, status="outcome_unknown",
        version=1, provider_reference=None, failure_category="DocsSubmissionUncertainError",
        requested_at="2026-01-01T00:00:00+00:00", approved_at=None, executing_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00", updated_at="2026-01-01T00:00:01+00:00",
    )
    text = status_text(action)
    assert "COULD NOT BE CONFIRMED" in text
    assert "DO NOT RETRY AUTOMATICALLY" in text
    assert "MANUAL RECONCILIATION REQUIRED" in text
    # No document body/content and no provider secret is ever surfaced.
    assert "Body" not in text and "hash" not in text


def test_status_text_succeeded_has_no_reconciliation_language():
    action = WorkspaceAction(
        id=1, action_type="create_docs_file", prepared_action_id=1, dispatch_id="d", approval_id="a",
        payload_schema_version=1, payload_fingerprint="hash", account_namespace=None,
        idempotency_key="key", correlation_id="corr", requested_chat_id=None, status="succeeded",
        version=1, provider_reference="doc-1", failure_category=None,
        requested_at="2026-01-01T00:00:00+00:00", approved_at="2026-01-01T00:00:00+00:00",
        executing_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:00:01+00:00",
        updated_at="2026-01-01T00:00:01+00:00",
    )
    text = status_text(action)
    assert text == "Workspace action #1: succeeded."


def test_build_application_registers_workspace_action_commands_before_text_fallback(tmp_path):
    settings = SimpleNamespace(telegram_bot_token="token", telegram_allowed_user_id=7)
    app = build_application(settings, SimpleNamespace(), workspace_actions=SimpleNamespace())
    handlers = app.handlers[0]
    command_indexes = {next(iter(handler.commands)): index for index, handler in enumerate(handlers) if hasattr(handler, "commands")}
    assert "createdoc" in command_indexes and "docstatus" in command_indexes
    assert command_indexes["createdoc"] < len(handlers) - 1
