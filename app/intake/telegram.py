"""Telegram transport for manual external-message intake."""

from __future__ import annotations

from typing import Any

from app.intake.models import SOURCE_CHANNEL_WHATSAPP_MANUAL
from app.intake.service import IntakeError, IntakeSensitiveContentError, IntakeStaleStateError
from app.security import is_authorized_user

UNAUTHORIZED_MESSAGE = "Akses ditolak. NOVA adalah AI Office pribadi."
WACONFIRM_USAGE = "Usage: /waconfirm <id> <work_item|note|knowledge_item> [field=value|field=value...]"


def _bot_data(context: Any) -> Any:
    bot_data = getattr(context, "bot_data", None)
    if bot_data is None:
        bot_data = getattr(getattr(context, "application", None), "bot_data", {})
    return bot_data


def _is_authorized(update: Any, bot_data: Any) -> bool:
    """Fail closed: an unresolvable authorization identity is never treated as authorized."""
    settings = bot_data.get("settings")
    if settings is None:
        return False
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    return is_authorized_user(user_id, settings.telegram_allowed_user_id)


async def wa_command(update: Any, context: Any) -> None:
    """Capture `/wa` text without contacting WhatsApp or auto-promoting data."""
    message = getattr(update, "effective_message", None)
    bot_data = _bot_data(context)
    if not _is_authorized(update, bot_data):
        await message.reply_text(UNAUTHORIZED_MESSAGE)
        return
    intake = bot_data.get("intake")
    if intake is None:
        await message.reply_text("External message intake is currently unavailable.")
        return
    args = list(getattr(context, "args", []) or [])
    if not args:
        await message.reply_text("Usage: /wa <pasted WhatsApp text>")
        return
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    try:
        record = intake.capture(
            SOURCE_CHANNEL_WHATSAPP_MANUAL,
            " ".join(args),
            str(user_id),
            telegram_update_id=str(getattr(update, "update_id", "")) or None,
        )
    except IntakeSensitiveContentError:
        await message.reply_text("Message intake was rejected because it may contain sensitive content.")
        return
    except IntakeError:
        await message.reply_text("Message intake failed. Please provide safe text and try again.")
        return
    label = record.classification.replace("_", " ").upper()
    if record.classification == "uncertain":
        await message.reply_text(
            f"Captured external message #{record.id}. Classification is uncertain; please clarify whether it is a task, note, knowledge, or follow-up."
        )
        return
    await message.reply_text(
        f"Captured external message #{record.id}. Classification (inference): {label}. "
        f"Nothing was created yet; confirm explicitly with /waconfirm {record.id} <work_item|note|knowledge_item>."
    )


async def waconfirm_command(update: Any, context: Any) -> None:
    """Fallback promotion command for when 8G's conversational confirmation is absent; delegates solely to `IntakeService.confirm()`."""
    message = getattr(update, "effective_message", None)
    bot_data = _bot_data(context)
    if not _is_authorized(update, bot_data):
        await message.reply_text(UNAUTHORIZED_MESSAGE)
        return
    intake = bot_data.get("intake")
    if intake is None:
        await message.reply_text("External message intake is currently unavailable.")
        return
    args = list(getattr(context, "args", []) or [])
    if len(args) < 2:
        await message.reply_text(WACONFIRM_USAGE)
        return
    intake_id_raw, target_type, *rest = args
    try:
        intake_id = int(intake_id_raw)
    except ValueError:
        await message.reply_text(WACONFIRM_USAGE)
        return
    fields: dict[str, str] = {}
    for pair in " ".join(rest).split("|"):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            await message.reply_text("Fields must be provided as field=value, separated by |.")
            return
        key, _, value = pair.partition("=")
        fields[key.strip()] = value.strip()
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    try:
        record = intake.confirm(intake_id, target_type, str(user_id), **fields)
    except IntakeStaleStateError:
        await message.reply_text(f"External message #{intake_id} is no longer pending and cannot be confirmed again.")
        return
    except IntakeError:
        await message.reply_text("Confirmation failed. Check the id, target type, and required fields, then try again.")
        return
    await message.reply_text(
        f"Confirmed external message #{record.id} as {record.target_type} ({record.target_id})."
    )
