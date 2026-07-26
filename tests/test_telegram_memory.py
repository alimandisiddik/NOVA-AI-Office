import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.telegram_bot import (
    UNAUTHORIZED_MESSAGE,
    continue_command,
    decision_command,
    help_command,
    handle_text,
    progress_command,
    project_command,
    projects_command,
    resume_command,
    start,
    status,
    task_command,
)


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id: int, text: str = "") -> None:
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_message = FakeMessage(text)


def context(memory, args: list[str] | None = None):
    settings = Settings("test-token", 7, "test", Path("/tmp/nova-test.db"))
    return SimpleNamespace(
        args=args or [],
        application=SimpleNamespace(bot_data={"settings": settings, "memory": memory}),
    )


def run(handler, update, test_context) -> str:
    asyncio.run(handler(update, test_context))
    return update.effective_message.replies[-1]


def test_telegram_memory_commands_and_sprint_one_compatibility(tmp_path) -> None:
    memory = WorkspaceMemoryService(MemoryDatabase(tmp_path / "memory.sqlite3"))
    memory.initialize()

    assert "Belum ada project" in run(projects_command, FakeUpdate(7), context(memory))
    assert "Usage" in run(project_command, FakeUpdate(7), context(memory, []))
    assert "Project created" in run(
        project_command,
        FakeUpdate(7),
        context(memory, ["NOVA", "AI", "Office", "|", "Workspace", "Memory"]),
    )
    assert "Task created" in run(
        task_command,
        FakeUpdate(7),
        context(memory, ["NOVA", "AI", "Office", "|", "Build", "database", "|", "high"]),
    )
    assert "Decision stored" in run(
        decision_command,
        FakeUpdate(7),
        context(memory, ["NOVA", "AI", "Office", "|", "Use", "SQLite"]),
    )
    memory.create_session("NOVA AI Office", "Database ready", "Schema", "Add tests")
    assert "Resume — NOVA AI Office" in run(
        resume_command, FakeUpdate(7), context(memory, ["NOVA", "AI", "Office"])
    )
    assert "Completion:" in run(
        progress_command, FakeUpdate(7), context(memory, ["NOVA", "AI", "Office"])
    )
    assert "Continue — NOVA AI Office" in run(
        continue_command, FakeUpdate(7), context(memory, ["NOVA", "AI", "Office"])
    )
    assert run(start, FakeUpdate(7), context(memory)) == "Halo Prof.\n\nSaya NOVA — Your Executive AI Office.\nSaya siap membantu."
    assert "Perintah NOVA:" in run(help_command, FakeUpdate(7), context(memory))
    assert "NOVA System Status" in run(status, FakeUpdate(7), context(memory))
    assert "Perintah diterima:" in run(handle_text, FakeUpdate(7, "hello"), context(memory))


def test_unauthorized_telegram_user_cannot_access_memory(tmp_path) -> None:
    memory = WorkspaceMemoryService(MemoryDatabase(tmp_path / "memory.sqlite3"))
    memory.initialize()

    assert run(projects_command, FakeUpdate(99), context(memory)) == UNAUTHORIZED_MESSAGE
    assert memory.list_projects() == []
