"""Read-only Telegram review and explicit-commit commands for Workspace candidates."""

from __future__ import annotations

from typing import Any

from app.security import is_authorized_user
from app.workspace_bridge.service import WorkspaceBridgeError

UNAUTHORIZED_MESSAGE = "Akses ditolak. NOVA adalah AI Office pribadi."
WORKSPACE_COMMIT_USAGE = "Usage: /workspacecommit <ref_id> <work_item|decision|knowledge_item>"


def _bot_data(context: Any) -> Any:
    return getattr(context, "bot_data", None) or getattr(getattr(context, "application", None), "bot_data", {})


def _is_authorized(update: Any, bot_data: Any) -> bool:
    settings = bot_data.get("settings")
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    return settings is not None and is_authorized_user(user_id, settings.telegram_allowed_user_id)


async def workspacecandidates_command(update: Any, context: Any) -> None:
    message = getattr(update, "effective_message", None)
    bot_data = _bot_data(context)
    if not _is_authorized(update, bot_data):
        await message.reply_text(UNAUTHORIZED_MESSAGE)
        return
    bridge = bot_data.get("workspace_bridge")
    if bridge is None:
        await message.reply_text("Workspace candidate bridge is currently unavailable.")
        return
    candidates = bridge.list_candidates()
    if not candidates:
        await message.reply_text("No Workspace candidates are awaiting review.")
        return
    rendered = [f"#{ref.id} — {ref.candidate_summary}\nSource: {ref.source_system}/{ref.external_source_type}" for ref in candidates]
    await message.reply_text("Workspace candidates (inference; nothing has been created):\n" + "\n\n".join(rendered))


async def workspacecommit_command(update: Any, context: Any) -> None:
    message = getattr(update, "effective_message", None)
    bot_data = _bot_data(context)
    if not _is_authorized(update, bot_data):
        await message.reply_text(UNAUTHORIZED_MESSAGE)
        return
    bridge = bot_data.get("workspace_bridge")
    if bridge is None:
        await message.reply_text("Workspace candidate bridge is currently unavailable.")
        return
    args = list(getattr(context, "args", []) or [])
    if len(args) != 2:
        await message.reply_text(WORKSPACE_COMMIT_USAGE)
        return
    try:
        ref_id = int(args[0])
        ref = bridge.commit(ref_id, args[1], str(getattr(getattr(update, "effective_user", None), "id", "")))
    except (ValueError, WorkspaceBridgeError):
        await message.reply_text("Workspace candidate could not be committed. Check the id and target type, then try again.")
        return
    await message.reply_text(f"Committed Workspace candidate #{ref.id} as {ref.target_type} ({ref.target_id}).")
