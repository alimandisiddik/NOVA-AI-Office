"""Isolated Telegram commands for the Workspace connector."""

from __future__ import annotations

from typing import Any

from app.google_workspace.status import get_workspace_capability_report
from app.security import is_authorized_user

_UNAUTHORIZED_MESSAGE = "Akses ditolak. NOVA adalah AI Office pribadi."


async def workspacestatus_command(update: Any, context: Any) -> None:
    """Render a non-sensitive status report without requiring shared bootstrap wiring."""
    message = getattr(update, "effective_message", None)
    if message is None:
        return
    settings = context.bot_data.get("settings")
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    allowed_user_id = getattr(settings, "telegram_allowed_user_id", None)
    if not is_authorized_user(user_id, allowed_user_id):
        await message.reply_text(_UNAUTHORIZED_MESSAGE)
        return
    bundle = context.bot_data.get("google_workspace")
    authenticator = getattr(bundle, "authenticator", None)
    report = get_workspace_capability_report(authenticator)
    lines = ["Google Workspace Status"]
    for service in report.services:
        state = "available" if service.available else service.reason
        lines.append(f"{service.service}: {state}")
    await message.reply_text("\n".join(lines))
