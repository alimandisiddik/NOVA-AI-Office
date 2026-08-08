"""Direct tests for the isolated Workspace status command handler."""

from __future__ import annotations

import asyncio

from app.google_workspace.telegram import workspacestatus_command


class Message:
    def __init__(self) -> None: self.replies: list[str] = []
    async def reply_text(self, text: str) -> None: self.replies.append(text)


class Update:
    def __init__(self, user_id: int) -> None:
        self.effective_message = Message()
        self.effective_user = type("User", (), {"id": user_id})()


class Context:
    def __init__(self, bot_data: dict[str, object]) -> None: self.bot_data = bot_data


def test_status_handler_is_safe_when_connector_is_absent() -> None:
    update = Update(7)
    asyncio.run(workspacestatus_command(
        update, Context({"settings": type("Settings", (), {"telegram_allowed_user_id": 7})()})
    ))
    assert "gmail: not_configured" in update.effective_message.replies[0]
    assert "token" not in update.effective_message.replies[0].lower()


def test_status_handler_rejects_unauthorized_user() -> None:
    update = Update(8)
    asyncio.run(workspacestatus_command(
        update, Context({"settings": type("Settings", (), {"telegram_allowed_user_id": 7})()})
    ))
    assert update.effective_message.replies == ["Akses ditolak. NOVA adalah AI Office pribadi."]
