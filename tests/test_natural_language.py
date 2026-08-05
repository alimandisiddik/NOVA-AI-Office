import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.natural_language import parse_workspace_intent
from app.telegram_bot import UNAUTHORIZED_MESSAGE, handle_text


@pytest.mark.parametrize(
    ("message", "action"),
    [
        ("Buat project ENFI Content System", "create_project"),
        ("List projects", "list_projects"),
        ("Tambahkan task membuat kalender konten di project NUO Trust Fund", "create_task"),
        ("Tampilkan task di project NUO Trust Fund", "list_tasks"),
        ("Tandai task Workspace Memory selesai di project NOVA AI Office", "update_task_status"),
        ("Catat note SQLite dipilih di project NOVA AI Office", "create_note"),
        ("Catat keputusan bahwa Google Drive bukan prioritas di project NOVA AI Office", "create_decision"),
        ("Rekam sesi testing selesai di project NOVA AI Office", "create_session"),
        ("Apa progress project NOVA AI Office?", "show_progress"),
        ("Resume project NOVA AI Office", "resume_project"),
        ("Lanjutkan project NOVA AI Office", "continue_project"),
    ],
)
def test_parser_recognizes_supported_workspace_intents(message, action) -> None:
    intent = parse_workspace_intent(message)

    assert intent is not None
    assert intent.action == action
    assert intent.clarification is None


def test_parser_requests_clarification_for_missing_project_context() -> None:
    assert parse_workspace_intent("Tandai task Workspace Memory selesai").clarification
    assert parse_workspace_intent("Catat keputusan bahwa Google Drive bukan prioritas").clarification
    assert parse_workspace_intent("Buat project").clarification


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, user_id: int, text: str) -> None:
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_message = FakeMessage(text)


def _context(memory: WorkspaceMemoryService):
    return SimpleNamespace(
        args=[],
        application=SimpleNamespace(
            bot_data={"settings": Settings("test-token", 7, "test", Path("/tmp/nova-test.db")), "memory": memory}
        ),
    )


def _send(memory: WorkspaceMemoryService, text: str, user_id: int = 7) -> str:
    update = FakeUpdate(user_id, text)
    asyncio.run(handle_text(update, _context(memory)))
    return update.effective_message.replies[-1]


def test_authorized_natural_language_routes_to_workspace_memory(tmp_path) -> None:
    memory = WorkspaceMemoryService(MemoryDatabase(tmp_path / "memory.sqlite3"))
    memory.initialize()

    assert "Project created" in _send(memory, "Buat project NOVA AI Office")
    assert "Task created" in _send(memory, "Tambahkan task build parser di project NOVA AI Office")
    assert "Task updated" in _send(memory, "Tandai task build parser selesai di project NOVA AI Office")
    assert "Note stored" in _send(memory, "Catat note parser deterministic di project NOVA AI Office")
    assert "Decision stored" in _send(
        memory, "Catat keputusan bahwa tanpa API eksternal di project NOVA AI Office"
    )
    assert "Work session recorded" in _send(memory, "Rekam sesi parser selesai di project NOVA AI Office")
    assert "Progress — NOVA AI Office" in _send(memory, "Apa progress project NOVA AI Office?")
    assert "Resume — NOVA AI Office" in _send(memory, "Resume project NOVA AI Office")
    assert "Continue — NOVA AI Office" in _send(memory, "Lanjutkan project NOVA AI Office")


def test_natural_language_keeps_authorization_and_returns_clarification(tmp_path) -> None:
    memory = WorkspaceMemoryService(MemoryDatabase(tmp_path / "memory.sqlite3"))
    memory.initialize()

    assert _send(memory, "Buat project Tidak Boleh", user_id=99) == UNAUTHORIZED_MESSAGE
    assert memory.list_projects() == []
    assert "Untuk project mana" in _send(memory, "Tandai task Workspace Memory selesai")
