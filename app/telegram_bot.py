"""Telegram handlers for the local NOVA bot."""

from __future__ import annotations

import logging
from collections.abc import Callable
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
from app.memory.database import MemoryDatabaseError
from app.memory.formatters import (
    continue_message,
    progress_message,
    projects_message,
    resume_message,
    tasks_message,
)
from app.memory.repositories import (
    DuplicateProjectError,
    InvalidMemoryValueError,
    MemoryError,
    ProjectNotFoundError,
)
from app.memory.services import WorkspaceMemoryService
from app.security import is_authorized_user

LOGGER = logging.getLogger(__name__)

UNAUTHORIZED_MESSAGE: Final = "Akses ditolak. NOVA adalah AI Office pribadi."
START_MESSAGE: Final = (
    "Halo Prof.\n\n"
    "Saya NOVA — Your Executive AI Office.\n"
    "Saya siap membantu."
)
HELP_MESSAGE: Final = (
    "Perintah NOVA:\n"
    "/start, /help, /status\n"
    "/project, /projects, /task, /tasks, /note, /decision\n"
    "/resume, /progress, /continue"
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


def _memory(context: ContextTypes.DEFAULT_TYPE) -> WorkspaceMemoryService:
    return cast(WorkspaceMemoryService, context.application.bot_data["memory"])


async def _require_authorized_user(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    message = update.effective_message
    user = update.effective_user

    if message is None:
        LOGGER.warning("Ignored Telegram update without a reply target.")
        return False

    if not is_authorized_user(
        user.id if user else None, _settings(context).telegram_allowed_user_id
    ):
        LOGGER.warning("Rejected unauthorized Telegram access attempt.")
        await message.reply_text(UNAUTHORIZED_MESSAGE)
        return False

    return True


def _argument_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    return " ".join(context.args).strip()


def _pipe_fields(raw: str, minimum: int, maximum: int) -> list[str] | None:
    fields = [field.strip() for field in raw.split("|")]
    if not raw or not minimum <= len(fields) <= maximum or any(not field for field in fields):
        return None
    return fields


async def _memory_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    operation: Callable[[], str],
) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    if message is None:
        return
    try:
        response = operation()
    except DuplicateProjectError:
        response = "Project sudah ada. Gunakan nama project yang berbeda."
    except ProjectNotFoundError:
        response = "Project tidak ditemukan. Periksa nama project dan coba lagi."
    except InvalidMemoryValueError as error:
        response = str(error)
    except ValueError:
        response = "Input tidak valid. Gunakan format perintah yang ditampilkan."
    except (MemoryError, MemoryDatabaseError):
        LOGGER.error("Workspace Memory command failed: %s", "domain_or_database_error")
        response = "Workspace Memory sedang tidak tersedia. Coba lagi nanti."
    except Exception:
        LOGGER.error("Unexpected Workspace Memory command failure.")
        response = "Terjadi kesalahan internal. Coba lagi nanti."
    await message.reply_text(response)


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


async def project_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fields = _pipe_fields(_argument_text(context), 1, 2)
    if fields is None:
        await _memory_reply(update, context, lambda: "Usage: /project Nama Project | deskripsi")
        return

    def operation() -> str:
        project = _memory(context).create_project(fields[0], fields[1] if len(fields) == 2 else "")
        return f"Project created\n\nName: {project.name}\nStatus: {project.status}"

    await _memory_reply(update, context, operation)


async def projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if _argument_text(context):
        await _memory_reply(update, context, lambda: "Usage: /projects")
        return
    await _memory_reply(update, context, lambda: projects_message(_memory(context).list_projects()))


async def task_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fields = _pipe_fields(_argument_text(context), 2, 3)
    if fields is None:
        await _memory_reply(update, context, lambda: "Usage: /task Nama Project | judul task | priority")
        return

    def operation() -> str:
        task = _memory(context).create_task(fields[0], fields[1], fields[2] if len(fields) == 3 else "normal")
        return f"Task created\n\nProject: {fields[0]}\nTask: {task.title}\nPriority: {task.priority}"

    await _memory_reply(update, context, operation)


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fields = _pipe_fields(_argument_text(context), 1, 2)
    if fields is None:
        await _memory_reply(update, context, lambda: "Usage: /tasks Nama Project | status")
        return
    await _memory_reply(
        update,
        context,
        lambda: tasks_message(*_memory(context).list_tasks(fields[0], fields[1] if len(fields) == 2 else None)),
    )


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fields = _pipe_fields(_argument_text(context), 2, 2)
    if fields is None:
        await _memory_reply(update, context, lambda: "Usage: /note Nama Project | isi catatan")
        return

    def operation() -> str:
        _memory(context).create_note(fields[0], fields[1])
        return f"Note stored for {fields[0]}."

    await _memory_reply(update, context, operation)


async def decision_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fields = _pipe_fields(_argument_text(context), 2, 3)
    if fields is None:
        await _memory_reply(update, context, lambda: "Usage: /decision Nama Project | keputusan | alasan")
        return

    def operation() -> str:
        _memory(context).create_decision(fields[0], fields[1], fields[2] if len(fields) == 3 else None)
        return f"Decision stored for {fields[0]}."

    await _memory_reply(update, context, operation)


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    project_name = _argument_text(context)
    if not project_name:
        await _memory_reply(update, context, lambda: "Usage: /resume Nama Project")
        return
    await _memory_reply(update, context, lambda: resume_message(_memory(context).resume(project_name)))


async def progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    project_name = _argument_text(context)
    if not project_name:
        await _memory_reply(update, context, lambda: "Usage: /progress Nama Project")
        return
    await _memory_reply(update, context, lambda: progress_message(*_memory(context).progress(project_name)))


async def continue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    project_name = _argument_text(context)
    if not project_name:
        await _memory_reply(update, context, lambda: "Usage: /continue Nama Project")
        return
    await _memory_reply(update, context, lambda: continue_message(_memory(context).continue_context(project_name)))


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


def build_application(settings: Settings, memory: WorkspaceMemoryService) -> Application:
    """Build the local polling application with scoped command handlers."""
    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.bot_data["settings"] = settings
    application.bot_data["memory"] = memory
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("project", project_command))
    application.add_handler(CommandHandler("projects", projects_command))
    application.add_handler(CommandHandler("task", task_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("note", note_command))
    application.add_handler(CommandHandler("decision", decision_command))
    application.add_handler(CommandHandler("resume", resume_command))
    application.add_handler(CommandHandler("progress", progress_command))
    application.add_handler(CommandHandler("continue", continue_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(handle_error)
    return application
