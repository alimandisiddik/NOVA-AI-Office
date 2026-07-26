"""Telegram handlers for the local NOVA bot."""

from __future__ import annotations

import logging
from typing import Final, cast

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import Settings
from app.security import is_authorized_user

LOGGER = logging.getLogger(__name__)

UNAUTHORIZED_MESSAGE: Final = "Akses ditolak. NOVA adalah AI Office pribadi."
START_MESSAGE: Final = (
    "Halo Prof.\n\n"
    "Saya NOVA — Your Executive AI Office.\n"
    "Saya siap membantu."
)
HELP_MESSAGE: Final = (
    "Perintah NOVA yang tersedia:\n"
    "/start — Mulai NOVA\n"
    "/help — Tampilkan bantuan\n"
    "/status — Lihat status sistem"
)
STATUS_MESSAGE: Final = (
    "NOVA System Status\n\n"
    "✅ NOVA Core: Ready\n"
    "✅ Telegram Bot: Ready\n"
    "✅ GitHub Repository: Ready\n"
    "⏳ Google Drive: Not Connected\n"
    "⏳ Gmail: Not Connected\n"
    "⏳ Calendar: Not Connected"
)


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return cast(Settings, context.application.bot_data["settings"])


async def _require_authorized_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    message = update.effective_message
    user = update.effective_user

    if message is None:
        LOGGER.warning("Ignored Telegram update without a reply target.")
        return False

    if not is_authorized_user(user.id if user else None, _settings(context).telegram_allowed_user_id):
        LOGGER.warning("Rejected unauthorized Telegram access attempt.")
        await message.reply_text(UNAUTHORIZED_MESSAGE)
        return False

    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome the configured NOVA user."""
    if not await _require_authorized_user(update, context):
        return

    message = update.effective_message
    if message is not None:
        await message.reply_text(START_MESSAGE)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show available local NOVA commands."""
    if not await _require_authorized_user(update, context):
        return

    message = update.effective_message
    if message is not None:
        await message.reply_text(HELP_MESSAGE)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Report the current local NOVA integration status."""
    if not await _require_authorized_user(update, context):
        return

    message = update.effective_message
    if message is not None:
        await message.reply_text(STATUS_MESSAGE)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Acknowledge text from the configured NOVA user without logging its content."""
    if not await _require_authorized_user(update, context):
        return

    message = update.effective_message
    if message is None:
        return

    user_message = message.text or ""
    response = f"Perintah diterima:\n\n{user_message}\n\nNOVA saat ini masih dalam Sprint 1."
    await message.reply_text(response)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record a generic failure without leaking update data, credentials, or tokens."""
    del update, context
    LOGGER.error("Unhandled Telegram bot error.")


def build_application(settings: Settings) -> Application:
    """Build the local polling application with scoped command handlers."""
    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.bot_data["settings"] = settings

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(handle_error)
    return application
