"""Read-only Telegram rendering for local draft preparation."""

from __future__ import annotations

from typing import Any

from app.drafting.service import (
    DraftingError,
    DraftingNotFoundError,
    DraftingSensitiveContentError,
    DraftingService,
)
from app.security import is_authorized_user


UNAUTHORIZED_MESSAGE = "Akses ditolak. NOVA adalah AI Office pribadi."


def _bot_data(context: Any) -> Any:
    bot_data = getattr(context, "bot_data", None)
    return bot_data if bot_data is not None else getattr(getattr(context, "application", None), "bot_data", {})


def _authorized(update: Any, bot_data: Any) -> bool:
    settings = bot_data.get("settings")
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    return settings is not None and is_authorized_user(user_id, settings.telegram_allowed_user_id)


def _fields(context: Any, minimum: int, maximum: int) -> list[str] | None:
    fields = [field.strip() for field in " ".join(getattr(context, "args", []) or []).split("|")]
    return fields if minimum <= len(fields) <= maximum and all(fields) else None


def _service(bot_data: Any) -> DraftingService | None:
    service = bot_data.get("drafting")
    return service if service is not None else None


async def draftreply_command(update: Any, context: Any) -> None:
    await _prepare(update, context, "Usage: /draftreply <message_id> | <instructions>", "reply")


async def draftmemo_command(update: Any, context: Any) -> None:
    await _prepare(update, context, "Usage: /draftmemo <title> | <instructions>", "memo")


async def draftsheet_command(update: Any, context: Any) -> None:
    await _prepare(update, context, "Usage: /draftsheet <file_id> | <a1_range> | <instructions>", "sheet")


async def draftslides_command(update: Any, context: Any) -> None:
    await _prepare(update, context, "Usage: /draftslides <title> | <instructions>", "slides")


async def _prepare(update: Any, context: Any, usage: str, kind: str) -> None:
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    bot_data = _bot_data(context)
    if not _authorized(update, bot_data):
        await message.reply_text(UNAUTHORIZED_MESSAGE)
        return
    service = _service(bot_data)
    if service is None:
        await message.reply_text("Drafting is temporarily unavailable.")
        return
    expected = 3 if kind == "sheet" else 2
    fields = _fields(context, expected, expected)
    if fields is None:
        await message.reply_text(usage)
        return
    actor = str(getattr(getattr(update, "effective_user", None), "id", ""))
    try:
        if kind == "reply":
            action = service.prepare_gmail_reply(fields[0], fields[1], actor)
        elif kind == "memo":
            action = service.prepare_docs_memo(fields[0], fields[1], actor)
        elif kind == "sheet":
            action = service.prepare_sheets_change(fields[0], fields[1], fields[2], actor)
        else:
            action = service.prepare_slides_outline(fields[0], fields[1], actor)
    except DraftingSensitiveContentError:
        await message.reply_text("Draft was rejected because it may contain sensitive content.")
        return
    except DraftingError:
        await message.reply_text(usage)
        return
    await message.reply_text(f"DRAFT #{action.id} prepared locally. Nothing was sent, published, or changed externally.")


async def drafts_command(update: Any, context: Any) -> None:
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    bot_data = _bot_data(context)
    if not _authorized(update, bot_data):
        await message.reply_text(UNAUTHORIZED_MESSAGE)
        return
    service = _service(bot_data)
    if service is None:
        await message.reply_text("Drafting is temporarily unavailable.")
        return
    actions = service.list_actions(limit=20)
    lines = ["NOVA — LOCAL DRAFTS"]
    lines.extend(f"DRAFT #{item.id} — {item.content_type} — {item.status}" for item in actions)
    await message.reply_text("\n".join(lines) if actions else "NOVA — LOCAL DRAFTS\nNo local drafts are available.")


async def draft_command(update: Any, context: Any) -> None:
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    bot_data = _bot_data(context)
    if not _authorized(update, bot_data):
        await message.reply_text(UNAUTHORIZED_MESSAGE)
        return
    service = _service(bot_data)
    args = list(getattr(context, "args", []) or [])
    if service is None:
        await message.reply_text("Drafting is temporarily unavailable.")
        return
    if len(args) != 1:
        await message.reply_text("Usage: /draft <id>")
        return
    try:
        action = service.get_action(int(args[0]))
    except (ValueError, DraftingNotFoundError):
        await message.reply_text("Draft was not found.")
        return
    except DraftingError:
        await message.reply_text("Usage: /draft <id>")
        return
    content = action.body_text if action.body_text is not None else action.body_payload or ""
    rendered = f"DRAFT #{action.id} — {action.content_type} — {action.status}\n{content}"
    await message.reply_text(rendered[:3_800])
