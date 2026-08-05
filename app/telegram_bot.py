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
    sessions_message,
    tasks_message,
)
from app.memory.repositories import (
    AmbiguousTaskError,
    DuplicateProjectError,
    InvalidMemoryValueError,
    InvalidTaskStatusError,
    MemoryError,
    ProjectNotFoundError,
    TaskNotFoundError,
    TaskStatusUnchangedError,
)
from app.memory.services import WorkspaceMemoryService
from app.natural_language import WorkspaceIntent, parse_workspace_intent
from app.security import is_authorized_user
from app.router.planner import (
    format_plan,
    format_route,
    format_router_status,
    generate_plan,
)
from app.router.roles import list_roles
from app.router.workflows import list_workflows
from app.execution.service import ExecutionService, SensitiveContentError
from app.execution.repository import (
    ApprovalError,
    ExecutionNotFoundError,
    InvalidTransitionError,
)
from app.execution.formatters import (
    execution_approved_message,
    execution_cancelled_message,
    execution_created_message,
    execution_status_message,
)

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
    "/resume, /progress, /continue\n"
    "/route <pesan> — klasifikasi cepat\n"
    "/plan <pesan> — rencana eksekusi lengkap\n"
    "/router_status — status router\n"
    "/run <instruksi> — buat execution\n"
    "/runapprove <id> — setujui execution\n"
    "/runstatus <id> — cek status execution\n"
    "/cancelrun <id> — batalkan execution\n"
    "Anda juga dapat memakai bahasa natural sederhana."
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


def _execution_svc(context: ContextTypes.DEFAULT_TYPE) -> ExecutionService:
    return cast(ExecutionService, context.application.bot_data["execution"])


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
    await message.reply_text(_memory_operation_response(operation))


def _memory_operation_response(operation: Callable[[], str]) -> str:
    """Run a memory operation without exposing internal details to the user."""
    try:
        return operation()
    except DuplicateProjectError:
        return "Project sudah ada. Gunakan nama project yang berbeda."
    except ProjectNotFoundError:
        return "Project tidak ditemukan. Periksa nama project dan coba lagi."
    except TaskNotFoundError:
        return "Task tidak ditemukan untuk project ini."
    except AmbiguousTaskError as error:
        matches = ", ".join(f"#{task.id}" for task in error.tasks)
        return f"Beberapa task memiliki judul ini: {matches}. Gunakan ID numerik pada /task_status."
    except TaskStatusUnchangedError:
        return "Task status is already set. Tidak ada perubahan."
    except InvalidTaskStatusError:
        return "Status task tidak valid. Gunakan: todo, doing, done, atau cancelled."
    except InvalidMemoryValueError as error:
        return str(error)
    except ValueError:
        return "Input tidak valid. Gunakan format perintah yang ditampilkan."
    except (MemoryError, MemoryDatabaseError):
        LOGGER.error("Workspace Memory command failed: %s", "domain_or_database_error")
        return "Workspace Memory sedang tidak tersedia. Coba lagi nanti."
    except Exception:
        LOGGER.error("Unexpected Workspace Memory command failure.")
        return "Terjadi kesalahan internal. Coba lagi nanti."


def _natural_language_response(intent: WorkspaceIntent, memory: WorkspaceMemoryService) -> str:
    """Map a deterministic parsed intent to an existing Workspace Memory use case."""
    fields = intent.fields or {}
    if intent.action == "create_project":
        project = memory.create_project(fields["name"])
        return f"Project created\n\nName: {project.name}\nStatus: {project.status}"
    if intent.action == "list_projects":
        return projects_message(memory.list_projects())
    if intent.action == "create_task":
        task = memory.create_task(fields["project_name"], fields["title"])
        return f"Task created\n\nProject: {fields['project_name']}\nTask: {task.title}\nPriority: {task.priority}"
    if intent.action == "list_tasks":
        return tasks_message(*memory.list_tasks(fields["project_name"]))
    if intent.action == "update_task_status":
        update_result = memory.update_task_status_for_project(
            fields["project_name"], fields["task_identifier"], fields["status"]
        )
        task = update_result.task
        return (
            "Task updated\n\n"
            f"Project: {fields['project_name']}\n"
            f"Task: {task.title}\n"
            f"Previous status: {update_result.previous_status}\n"
            f"New status: {task.status}"
        )
    if intent.action == "create_note":
        memory.create_note(fields["project_name"], fields["content"])
        return f"Note stored for {fields['project_name']}."
    if intent.action == "create_decision":
        memory.create_decision(fields["project_name"], fields["decision"])
        return f"Decision stored for {fields['project_name']}."
    if intent.action == "create_session":
        session = memory.create_session(fields["project_name"], fields["summary"], "", "")
        return f"Work session recorded\n\nProject: {fields['project_name']}\nSummary: {session.summary}"
    if intent.action == "show_progress":
        return progress_message(*memory.progress(fields["project_name"]))
    if intent.action == "resume_project":
        return resume_message(memory.resume(fields["project_name"]))
    if intent.action == "continue_project":
        return continue_message(memory.continue_context(fields["project_name"]))
    return "Saya belum memahami perintah itu. Gunakan /help untuk daftar perintah."


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


async def task_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fields = _pipe_fields(_argument_text(context), 3, 3)
    if fields is None:
        await _memory_reply(update, context, lambda: "Usage: /task_status Nama Project | ID atau judul task | status")
        return

    def operation() -> str:
        update_result = _memory(context).update_task_status_for_project(fields[0], fields[1], fields[2])
        task = update_result.task
        return (
            "Task updated\n\n"
            f"Project: {fields[0]}\n"
            f"Task: {task.title}\n"
            f"Previous status: {update_result.previous_status}\n"
            f"New status: {task.status}"
        )

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


async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fields = _pipe_fields(_argument_text(context), 2, 4)
    if fields is None:
        await _memory_reply(
            update,
            context,
            lambda: "Usage: /session Nama Project | summary | completed items | next action",
        )
        return

    def operation() -> str:
        session = _memory(context).create_session(
            fields[0],
            fields[1],
            fields[2] if len(fields) >= 3 else "",
            fields[3] if len(fields) == 4 else "",
        )
        lines = ["Work session recorded", "", f"Project: {fields[0]}", f"Summary: {session.summary}"]
        if session.completed_items:
            lines.append(f"Completed: {session.completed_items}")
        if session.next_action:
            lines.append(f"Next action: {session.next_action}")
        return "\n".join(lines)

    await _memory_reply(update, context, operation)


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fields = _pipe_fields(_argument_text(context), 1, 2)
    if fields is None:
        await _memory_reply(update, context, lambda: "Usage: /sessions Nama Project | limit (1-10)")
        return
    if len(fields) == 2 and not fields[1].isdigit():
        await _memory_reply(update, context, lambda: "Session limit must be a number from 1 to 10.")
        return
    limit = int(fields[1]) if len(fields) == 2 else 5
    await _memory_reply(
        update,
        context,
        lambda: sessions_message(*_memory(context).list_recent_sessions(fields[0], limit)),
    )


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
    intent = parse_workspace_intent(user_message)
    if intent is not None:
        if intent.clarification:
            await message.reply_text(intent.clarification)
            return
        await message.reply_text(
            _memory_operation_response(lambda: _natural_language_response(intent, _memory(context)))
        )
        return
    response = f"Perintah diterima:\n\n{user_message}\n\nNOVA saat ini masih dalam Sprint 1."
    await message.reply_text(response)



async def route_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Classify a message and return a compact routing summary."""
    if not await _require_authorized_user(update, context):
        return
    effective_message = update.effective_message
    if effective_message is None:
        return
    message_text = _argument_text(context)
    if not message_text:
        await effective_message.reply_text("Usage: /route <pesan>")
        return
    plan = generate_plan(message_text)
    await effective_message.reply_text(format_route(plan))


async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and display a full execution plan for a message."""
    if not await _require_authorized_user(update, context):
        return
    effective_message = update.effective_message
    if effective_message is None:
        return
    message_text = _argument_text(context)
    if not message_text:
        await effective_message.reply_text("Usage: /plan <pesan>")
        return
    plan = generate_plan(message_text)
    await effective_message.reply_text(format_plan(plan))



async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create and dispatch (or queue for approval) an execution from /run <instruction>."""
    if not await _require_authorized_user(update, context):
        return
    effective_message = update.effective_message
    if effective_message is None:
        return
    instruction = _argument_text(context)
    if not instruction:
        await effective_message.reply_text("Usage: /run <instruksi>")
        return
    svc = _execution_svc(context)
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        record = svc.submit(instruction, user_id)
        await effective_message.reply_text(execution_created_message(record))
    except SensitiveContentError:
        await effective_message.reply_text(
            "Execution request ditolak: instruksi mengandung konten sensitif."
        )
    except Exception:
        LOGGER.error("run_command failed unexpectedly.")
        await effective_message.reply_text("Terjadi kesalahan internal. Coba lagi nanti.")


async def runapprove_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve an awaiting_approval execution: /runapprove <id>."""
    if not await _require_authorized_user(update, context):
        return
    effective_message = update.effective_message
    if effective_message is None:
        return
    raw_id = _argument_text(context)
    if not raw_id or not raw_id.isdigit():
        await effective_message.reply_text("Usage: /runapprove <execution_id>")
        return
    execution_id = int(raw_id)
    svc = _execution_svc(context)
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        record = svc.approve(execution_id, user_id)
        await effective_message.reply_text(execution_approved_message(record))
    except (ApprovalError, ExecutionNotFoundError) as exc:
        await effective_message.reply_text(f"Approval gagal: {exc}")
    except Exception:
        LOGGER.error("runapprove_command failed unexpectedly.")
        await effective_message.reply_text("Terjadi kesalahan internal. Coba lagi nanti.")


async def runstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return the current status of an execution: /runstatus <id>."""
    if not await _require_authorized_user(update, context):
        return
    effective_message = update.effective_message
    if effective_message is None:
        return
    raw_id = _argument_text(context)
    if not raw_id or not raw_id.isdigit():
        await effective_message.reply_text("Usage: /runstatus <execution_id>")
        return
    execution_id = int(raw_id)
    svc = _execution_svc(context)
    try:
        record = svc.get_status(execution_id)
        await effective_message.reply_text(execution_status_message(record))
    except ExecutionNotFoundError:
        await effective_message.reply_text(f"Execution #{execution_id} tidak ditemukan.")
    except Exception:
        LOGGER.error("runstatus_command failed unexpectedly.")
        await effective_message.reply_text("Terjadi kesalahan internal. Coba lagi nanti.")


async def cancelrun_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel (or reject) a non-terminal execution: /cancelrun <id>."""
    if not await _require_authorized_user(update, context):
        return
    effective_message = update.effective_message
    if effective_message is None:
        return
    raw_id = _argument_text(context)
    if not raw_id or not raw_id.isdigit():
        await effective_message.reply_text("Usage: /cancelrun <execution_id>")
        return
    execution_id = int(raw_id)
    svc = _execution_svc(context)
    user_id = update.effective_user.id if update.effective_user else 0
    try:
        record = svc.cancel(execution_id, user_id)
        await effective_message.reply_text(execution_cancelled_message(record))
    except InvalidTransitionError as exc:
        await effective_message.reply_text(f"Pembatalan tidak dapat dilakukan: {exc}")
    except ExecutionNotFoundError:
        await effective_message.reply_text(f"Execution #{execution_id} tidak ditemukan.")
    except Exception:
        LOGGER.error("cancelrun_command failed unexpectedly.")
        await effective_message.reply_text("Terjadi kesalahan internal. Coba lagi nanti.")

async def router_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the router status: all registered roles and workflows."""
    if not await _require_authorized_user(update, context):
        return
    effective_message = update.effective_message
    if effective_message is None:
        return
    await effective_message.reply_text(format_router_status(list_roles(), list_workflows()))

async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record a generic failure without leaking update data, credentials, or tokens."""
    del update, context
    LOGGER.error("Unhandled Telegram bot error.")


def build_application(
    settings: Settings,
    memory: WorkspaceMemoryService,
    execution: ExecutionService | None = None,
) -> Application:
    """Build the local polling application with scoped command handlers."""
    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.bot_data["settings"] = settings
    application.bot_data["memory"] = memory
    application.bot_data["execution"] = execution
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("project", project_command))
    application.add_handler(CommandHandler("projects", projects_command))
    application.add_handler(CommandHandler("task", task_command))
    application.add_handler(CommandHandler("task_status", task_status_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("note", note_command))
    application.add_handler(CommandHandler("decision", decision_command))
    application.add_handler(CommandHandler("session", session_command))
    application.add_handler(CommandHandler("sessions", sessions_command))
    application.add_handler(CommandHandler("resume", resume_command))
    application.add_handler(CommandHandler("progress", progress_command))
    application.add_handler(CommandHandler("continue", continue_command))
    application.add_handler(CommandHandler("route", route_command))
    application.add_handler(CommandHandler("plan", plan_command))
    application.add_handler(CommandHandler("router_status", router_status_command))
    application.add_handler(CommandHandler("run", run_command))
    application.add_handler(CommandHandler("runapprove", runapprove_command))
    application.add_handler(CommandHandler("runstatus", runstatus_command))
    application.add_handler(CommandHandler("cancelrun", cancelrun_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(handle_error)
    return application
