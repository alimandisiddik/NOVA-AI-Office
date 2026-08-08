from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from app.agent_assignment.service import AgentAssignmentService
from app.memory import MemoryDatabase, WorkspaceMemoryService
from app.config import Settings
from app.telegram_bot import handle_assignments, handle_assignmentstatus, build_application


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, text: str, user_id: int = 7) -> None:
        self.effective_message = FakeMessage(text)
        self.message = self.effective_message
        self.effective_user = SimpleNamespace(id=user_id)


class FakeContext:
    def __init__(self, assignments: object | None) -> None:
        self.application = SimpleNamespace(bot_data={
            "settings": SimpleNamespace(telegram_allowed_user_id=7),
            "agent_assignment_svc": assignments,
        })


def run(handler, update: FakeUpdate, context: FakeContext) -> str:
    asyncio.run(handler(update, context))
    return update.effective_message.replies[-1]


def test_assignment_commands_are_read_only_and_render_bounded_status() -> None:
    assignments = Mock(spec=AgentAssignmentService)
    assignments.list_assignments.return_value = [
        SimpleNamespace(assignment_id="assignment-123456", assigned_agent_id="document_agent", status="accepted")
    ]
    assignments.get_assignment.return_value = SimpleNamespace(
        assignment_id="assignment-123456", status="in_progress", assigned_agent_id="document_agent",
        requested_capability="draft_only", dispatch_id="dispatch-123456",
    )

    assert "Assignments:" in run(handle_assignments, FakeUpdate("/assignments"), FakeContext(assignments))
    assert "Status: in_progress" in run(
        handle_assignmentstatus, FakeUpdate("/assignmentstatus assignment-123456"), FakeContext(assignments)
    )
    assignments.assert_not_called()


def test_assignment_commands_use_existing_authorization_guard() -> None:
    assignments = Mock(spec=AgentAssignmentService)
    reply = run(handle_assignments, FakeUpdate("/assignments", user_id=8), FakeContext(assignments))
    assert "Akses ditolak" in reply
    assignments.list_assignments.assert_not_called()


def test_assignment_command_registration_is_additive(tmp_path) -> None:
    settings = Settings("token", 7, "test", tmp_path / "nova.sqlite3")
    memory = WorkspaceMemoryService(MemoryDatabase(tmp_path / "nova.sqlite3"))
    application = build_application(
        settings, memory, agent_assignments=Mock(spec=AgentAssignmentService)
    )
    commands = [handler.commands for group in application.handlers.values() for handler in group if hasattr(handler, "commands")]
    flattened = [command for command_group in commands for command in command_group]
    assert flattened.count("assignments") == 1
    assert flattened.count("assignmentstatus") == 1
