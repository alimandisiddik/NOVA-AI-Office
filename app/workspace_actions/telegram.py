"""Exact action-specific Telegram commands; no natural-language approval parsing."""

from __future__ import annotations

from typing import Any

from app.workspace_actions.models import WorkspaceAction
from app.workspace_actions.service import WorkspaceActionError
from app.security import is_authorized_user


def approval_text(action: WorkspaceAction) -> str:
    return f"1. Create private Google Doc #{action.id}\nApprove with /approve {action.approval_id}."


def status_text(action: WorkspaceAction) -> str:
    suffix = " RECONCILIATION REQUIRED." if action.status == "outcome_unknown" else ""
    return f"Workspace action #{action.id}: {action.status}.{suffix}"


def _service(context: Any):
    return getattr(context, "application", context).bot_data.get("workspace_actions")


def _authorized(update: Any, context: Any) -> bool:
    settings = getattr(context, "application", context).bot_data.get("settings")
    return settings is not None and is_authorized_user(getattr(getattr(update, "effective_user", None), "id", None), settings.telegram_allowed_user_id)


async def createdoc_command(update: Any, context: Any) -> None:
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    if not _authorized(update, context):
        await message.reply_text("Akses ditolak. NOVA adalah AI Office pribadi.")
        return
    if len(getattr(context, "args", []) or []) != 1:
        await message.reply_text("Usage: /createdoc <ready_docs_memo_id>")
        return
    service = _service(context)
    if service is None:
        await message.reply_text("Workspace document creation is unavailable.")
        return
    try:
        prepared_id = int(context.args[0])
        user_id = update.effective_user.id
        action = service.request_create_docs_file(
            prepared_id,
            idempotency_key=f"telegram-createdoc:{getattr(getattr(update, 'effective_chat', None), 'id', 0)}:{getattr(message, 'message_id', 0)}",
            actor=f"user:{user_id}",
            correlation_id=f"telegram:{getattr(getattr(update, 'effective_chat', None), 'id', 0)}:{getattr(message, 'message_id', 0)}",
            requested_chat_id=str(getattr(getattr(update, "effective_chat", None), "id", "")),
        )
    except (ValueError, WorkspaceActionError):
        await message.reply_text("Ready docs memo was not found or is no longer executable.")
        return
    await message.reply_text(approval_text(action))


async def docstatus_command(update: Any, context: Any) -> None:
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    if not _authorized(update, context):
        await message.reply_text("Akses ditolak. NOVA adalah AI Office pribadi.")
        return
    if len(getattr(context, "args", []) or []) != 1:
        await message.reply_text("Usage: /docstatus <workspace_action_id>")
        return
    service = _service(context)
    try:
        action = service.get_action(int(context.args[0])) if service is not None else None
    except ValueError:
        action = None
    await message.reply_text(status_text(action) if action is not None else "Workspace action was not found.")
