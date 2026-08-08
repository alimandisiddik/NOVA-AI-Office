from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.drafting import DraftingService
from app.drafting.telegram import draft_command, draftmemo_command, drafts_command, draftreply_command, draftsheet_command, draftslides_command
from app.memory.database import MemoryDatabase


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


def _service(tmp_path) -> DraftingService:
    service = DraftingService(MemoryDatabase(tmp_path / "nova.sqlite3"))
    service.initialize()
    return service


def _update(user_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_message=FakeMessage())


def _context(service: DraftingService | None, args: list[str]) -> SimpleNamespace:
    settings = SimpleNamespace(telegram_allowed_user_id=7)
    return SimpleNamespace(application=SimpleNamespace(bot_data={"settings": settings, "drafting": service}), args=args)


def test_draft_commands_prepare_only_local_artifacts(tmp_path) -> None:
    service = _service(tmp_path)
    cases = (
        (draftreply_command, ["message-1", "|", "Reply safely."]),
        (draftmemo_command, ["Memo", "|", "Summarize safely."]),
        (draftsheet_command, ["sheet-1", "|", "A1:B2", "|", "Propose values."]),
        (draftslides_command, ["Board", "|", "Outline safely."]),
    )
    for handler, args in cases:
        update = _update()
        asyncio.run(handler(update, _context(service, list(args))))
        assert update.effective_message.replies[-1].startswith("DRAFT #")
        assert "Nothing was sent, published, or changed externally." in update.effective_message.replies[-1]

    assert len(service.list_actions()) == 4


def test_draft_listing_and_detail_are_bounded_and_labeled(tmp_path) -> None:
    service = _service(tmp_path)
    action = service.prepare_docs_memo("Memo", "A short local draft.", "user:7")
    list_update = _update()
    detail_update = _update()

    asyncio.run(drafts_command(list_update, _context(service, [])))
    asyncio.run(draft_command(detail_update, _context(service, [str(action.id)])))

    assert "LOCAL DRAFTS" in list_update.effective_message.replies[-1]
    assert detail_update.effective_message.replies[-1].startswith(f"DRAFT #{action.id}")
    assert "DRAFT — LOCAL ONLY" in detail_update.effective_message.replies[-1]


def test_draft_commands_fail_closed_and_degrade_when_unwired(tmp_path) -> None:
    service = _service(tmp_path)
    denied = _update(user_id=8)
    unavailable = _update()
    bad_fields = _update()

    asyncio.run(draftreply_command(denied, _context(service, ["message-1", "|", "Reply"])))
    asyncio.run(draftreply_command(unavailable, _context(None, ["message-1", "|", "Reply"])))
    asyncio.run(draftreply_command(bad_fields, _context(service, ["message-1"])))

    assert denied.effective_message.replies == ["Akses ditolak. NOVA adalah AI Office pribadi."]
    assert unavailable.effective_message.replies == ["Drafting is temporarily unavailable."]
    assert bad_fields.effective_message.replies == ["Usage: /draftreply <message_id> | <instructions>"]
