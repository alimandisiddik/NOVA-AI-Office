from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from app.conversation import Choice, ConversationService
from app.memory import WorkspaceMemoryService
from app.memory.database import MemoryDatabase
from app.telegram_bot import handle_text


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


def test_handle_text_resolves_open_interaction_instead_of_falling_through(tmp_path: Path) -> None:
    memory = WorkspaceMemoryService(MemoryDatabase(tmp_path / "memory.sqlite3"))
    memory.initialize()
    conversation = ConversationService(MemoryDatabase(tmp_path / "conversation.sqlite3"))
    conversation.initialize()
    interaction = conversation.ask(
        "chat-1",
        7,
        "/test",
        "Confirm safe operation",
        [Choice(1, "Continue", "low", "continue-token")],
    )
    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(
            bot_data={
                "settings": SimpleNamespace(telegram_allowed_user_id=7, telegram_bot_token="test"),
                "memory": memory,
                "conversation": conversation,
            }
        ),
    )

    ambiguous_update = FakeUpdate(7, "maybe")
    asyncio.run(handle_text(ambiguous_update, context))
    assert "Please choose one of the listed options" in ambiguous_update.effective_message.replies[-1]

    update = FakeUpdate(7, "1")
    asyncio.run(handle_text(update, context))

    assert update.effective_message.replies == ["Selected: Continue"]


def test_handle_text_falls_through_to_default_when_no_interaction_exists(tmp_path: Path) -> None:
    memory = WorkspaceMemoryService(MemoryDatabase(tmp_path / "memory.sqlite3"))
    memory.initialize()
    conversation = ConversationService(MemoryDatabase(tmp_path / "conversation.sqlite3"))
    conversation.initialize()
    context = SimpleNamespace(
        args=[],
        application=SimpleNamespace(
            bot_data={
                "settings": SimpleNamespace(telegram_allowed_user_id=7, telegram_bot_token="test"),
                "memory": memory,
                "conversation": conversation,
            }
        ),
    )

    update = FakeUpdate(7, "1")
    asyncio.run(handle_text(update, context))

    assert "Perintah diterima:" in update.effective_message.replies[-1]
