"""Telegram handlers for the local NOVA bot."""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, cast

from telegram import Update
from telegram import Message, User
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import Settings
from app.dissertation.service import DissertationService
from app.dissertation.repository import (
    DissertationTargetNotFoundError,
    InvalidDissertationValueError,
)
from app.knowledge.service import InvalidKnowledgeValueError, KnowledgeService
from app.knowledge.repository import KnowledgeError
from app.memory.database import MemoryDatabaseError
from app.memory.formatters import (
    continue_message,
    progress_message,
    projects_message,
    resume_message,
    sessions_message,
    tasks_message,
)
from app.nightshift.service import NightShiftService

from app.nightshift.worker import NightShiftWorker

from app.control_tower.service import ControlTowerService

from app.dispatch.service import DispatchService
from app.dispatch.approvals import ApprovalService
from app.dispatch.models import DispatchRequest
from app.dispatch.errors import DispatchError, DispatchUnavailableError

from app.control_tower.repository import ControlTowerError, InvalidCategoryError, ValidationError
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
from app.security import SENSITIVE_CONTENT_PATTERN, is_authorized_user
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
from app.providers.service import ProviderGatewayService
from app.providers.registry import get_registered_model
from app.providers.selection import resolve_upstream_route_id
from app.providers.errors import ProviderError
from app.execution.formatters import (
    execution_approved_message,
    execution_cancelled_message,
    execution_created_message,
    execution_status_message,
)

LOGGER = logging.getLogger(__name__)

# Sprint 5G.4: word-boundary supplement to SENSITIVE_CONTENT_PATTERN for a
# runtime error message. The shared pattern already covers "api key",
# "private key", "password", "credential", and "KEY=value" assignment
# shapes; it deliberately does NOT match a bare "key" (too many legitimate
# non-secret uses, e.g. "duplicate primary key"), so a bare "key" mention
# alone is not redacted here either. "token"/"secret" alone have no such
# common legitimate use in an error message, so they are redacted as a
# supplement to the shared pattern.
_ERROR_SENSITIVE_KEYWORD_PATTERN = re.compile(r"\b(?:token|secret)\b", re.IGNORECASE)
# Strips ASCII control characters (newlines, carriage returns, tabs, etc.)
# so an attacker/upstream-controlled exception message can never forge
# additional log lines or corrupt log parsing.
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_ERROR_MESSAGE_MAX_LENGTH = 500

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
    "/capture, /today, /approvals, /shutdown, /morning\n"
    "/dispatch, /dispatches, /dispatchstatus, /approve, /reject, /canceldispatch, /retrydispatch\n"
    "/nightshift, /nightstatus, /nightqueue [cancel <job_id>], /wake\n"
    "/resume, /progress, /continue\n"
    "/route <pesan> — klasifikasi cepat\n"
    "/plan <pesan> — rencana eksekusi lengkap\n"
    "/router_status — status router\n"
    "/run <instruksi> — buat execution\n"
    "/runapprove <id> — setujui execution\n"
    "/runstatus <id> — cek status execution\n"
    "/cancelrun <id> — batalkan execution\n"
    "/dissertation [chapter <n>|gaps|next|tasks|evidence <n>|sources [n]|decisions]\n"
    "/dissertation addsource <chapter n|-> | judul | jenis | sitasi | lokator\n"
    "/dissertation addevidence <source id> | <chapter n|-> | ringkasan | lokator | keyakinan\n"
    "/knowledgesource <judul> | <jenis sumber> | <sitasi> [| <lokator>]\n"
    "/knowledgeitem <id sumber> | <judul> | <ringkasan> [| <tag>] [| <keyakinan>]\n"
    "/knowledgequery <kata kunci atau tag>\n"
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


def _knowledge(context: ContextTypes.DEFAULT_TYPE) -> KnowledgeService | None:
    return cast(KnowledgeService | None, context.application.bot_data.get("knowledge"))


def _provider_svc(context: ContextTypes.DEFAULT_TYPE) -> ProviderGatewayService | None:
    return context.application.bot_data.get("provider")

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
    """Preserve legacy pipe syntax; no-pipe syntax registers a Control Tower decision."""
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    if message is None:
        return
    raw = _argument_text(context)
    if not raw:
        await message.reply_text("Usage: /decision Project | decision | rationale OR /decision decision summary")
        return
    if "|" in raw:
        fields = _pipe_fields(raw, 2, 3)
        if fields is None:
            await message.reply_text("Usage: /decision Project | decision | rationale")
            return
        try:
            _memory(context).create_decision(fields[0], fields[1], fields[2] if len(fields) == 3 else None)
        except MemoryError:
            await message.reply_text("Decision could not be saved.")
            return
        await message.reply_text(f"Decision stored for {fields[0]}.")
        return
    service = _control_tower(context)
    if service is None:
        await message.reply_text("Control Tower is temporarily unavailable.")
        return
    try:
        decision = service.register_decision(raw, "Captured from Telegram", "Review required", "telegram user", "telegram")
    except ControlTowerError:
        await message.reply_text("Decision could not be recorded. Check the summary and try again.")
        return
    except Exception:
        LOGGER.exception("Control Tower decision handler failed")
        await message.reply_text("Decision could not be recorded right now.")
        return
    await message.reply_text(f"Control Tower decision recorded: {decision.decision_id[:8]}.")

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

async def ask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Read-only text generation via the provider gateway: /ask <instruction>"""
    if not await _require_authorized_user(update, context):
        return
    effective_message = update.effective_message
    if effective_message is None:
        return
    instruction = _argument_text(context)
    if not instruction:
        await effective_message.reply_text("Usage: /ask <pertanyaan/instruksi>")
        return

    provider = _provider_svc(context)
    if not provider:
        await effective_message.reply_text("Provider Gateway belum dikonfigurasi.")
        return

    user_id = update.effective_user.id if update.effective_user else 0
    await effective_message.reply_chat_action("typing")
    try:
        response_text = await provider.generate_text(instruction, user_id)
        await effective_message.reply_text(response_text)
    except ProviderError as exc:
        await effective_message.reply_text(f"Gagal: {exc}")
    except Exception as exc:
        LOGGER.error("ask_command failed unexpectedly.", exc_info=True)
        await effective_message.reply_text("Terjadi kesalahan internal. Coba lagi nanti.")

async def providerstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show safe configuration status for the provider gateway chains."""
    if not await _require_authorized_user(update, context):
        return
    effective_message = update.effective_message
    if effective_message is None:
        return

    provider = _provider_svc(context)
    if not provider:
        await effective_message.reply_text("Provider Gateway: Not Configured")
        return

    # Mask base URL hostname but keep scheme to show HTTPS
    from urllib.parse import urlparse
    parsed = urlparse(provider.base_url)
    safe_url = f"{parsed.scheme}://***" if parsed.scheme else "***"

    # Sprint 5G: report every configured combo group (not only the generic
    # chain), plus each specialist adapter's own availability — a stub that
    # is not configured/available must show as such, never as "active".
    model_lines = []
    seen_model_ids: set[str] = set()
    for group_name, group_priority in provider.combo_priorities.items():
        for model_id in group_priority:
            if model_id in seen_model_ids:
                continue
            seen_model_ids.add(model_id)
            model = get_registered_model(model_id)
            enabled = "enabled" if model and model.enabled else "disabled"
            provider_id = model.provider_id if model else "9Router"
            circuit_state = provider.circuit_breaker.get_state(f"{provider_id}:{model_id}").upper()
            try:
                adapter = provider._get_adapter(provider_id)
                available = "available" if adapter is not None and adapter.is_available() else "unavailable"
            except Exception:
                available = "unknown"
            # Sprint 5G.1: the internal NOVA alias (model_id) and the
            # upstream/provider route actually dispatched to are two
            # distinct identities -- never conflate them in status output.
            upstream = (
                resolve_upstream_route_id(model, provider.upstream_route_overrides)
                if model is not None
                else None
            )
            upstream_label = upstream if upstream else "UNCONFIGURED (excluded from chain)"
            model_lines.append(
                f"  - [{group_name}] alias={model_id} -> upstream_route={upstream_label}: {enabled}; "
                f"provider={provider_id}; availability={available}; circuit={circuit_state}"
            )

    specialist_lines = []
    for provider_id in ("Codex", "Claude"):
        adapter = provider._get_adapter(provider_id)
        if adapter is None:
            specialist_lines.append(f"  - {provider_id}: not configured (stub inactive)")
            continue
        try:
            available = "available" if adapter.is_available() else "unavailable (stub)"
        except Exception:
            available = "unknown"
        specialist_lines.append(f"  - {provider_id}: {available}")

    recent_success = provider.last_successful_model or "none"
    fallback_reason = provider.last_fallback_reason or "none"
    lines = [
        "🌐 Provider Gateway Chain Status",
        "",
        f"URL: {safe_url}",
        f"Generic Chain (Priority): {', '.join(provider.model_priority)}",
        "Registered Routes (internal alias only — never a real model identity):",
    ] + model_lines + [
        "",
        "Direct Specialist Adapters (safe stubs until configured):",
    ] + specialist_lines + [
        "",
        f"Most Recent Successful Alias (internal, not upstream): {recent_success}",
        f"Last Fallback Reason: {fallback_reason}",
    ]
    await effective_message.reply_text("\n".join(lines))


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Record a generic failure without leaking update data, credentials, or tokens.

    ``update`` is never read or logged -- it is the one object guaranteed to
    carry real chat/user content. This is the bot's last-resort error
    handler, so it must never itself raise: a malformed/missing ``context``,
    a missing/None ``context.error``, or an exception whose own ``__str__``
    raises must all fall back to a safe, generic log line rather than
    propagate.
    """
    del update
    try:
        error = getattr(context, "error", None)
        if error is None:
            LOGGER.error("Unhandled Telegram bot error.")
            return

        error_type = type(error).__name__
        try:
            error_msg = str(error)
        except Exception:
            error_msg = "<error message unavailable>"

        # Match on the raw message first: SENSITIVE_CONTENT_PATTERN's
        # "^KEY=value"-shaped alternative relies on real line starts, which
        # control-character stripping below would otherwise disturb.
        if SENSITIVE_CONTENT_PATTERN.search(error_msg) or _ERROR_SENSITIVE_KEYWORD_PATTERN.search(error_msg):
            LOGGER.error("Unhandled Telegram bot error (%s): [REDACTED SENSITIVE CONTENT]", error_type)
            return

        # Neutralize newlines/control characters (log-injection defense)
        # before bounding length; length is judged on the original message.
        safe_msg = _CONTROL_CHAR_PATTERN.sub(" ", error_msg)[:_ERROR_MESSAGE_MAX_LENGTH]
        if len(error_msg) > _ERROR_MESSAGE_MAX_LENGTH:
            safe_msg += "..."
        LOGGER.error("Unhandled Telegram bot error (%s): %s", error_type, safe_msg)
    except Exception:
        LOGGER.error("Unhandled Telegram bot error.")



def _control_tower(context: ContextTypes.DEFAULT_TYPE) -> ControlTowerService | None:
    service = context.application.bot_data.get("control_tower")
    return service if isinstance(service, ControlTowerService) else None

def _dissertation(context: ContextTypes.DEFAULT_TYPE) -> DissertationService | None:
    service = context.application.bot_data.get("dissertation")
    return service if isinstance(service, DissertationService) else None

def _dispatch_svc(context: ContextTypes.DEFAULT_TYPE) -> DispatchService:
    svc = context.application.bot_data.get("dispatch_svc")
    if svc is None:
        raise DispatchUnavailableError("Dispatch service is unavailable.")
    return cast(DispatchService, svc)

def _approval_svc(context: ContextTypes.DEFAULT_TYPE) -> ApprovalService:
    svc = context.application.bot_data.get("approval_svc")
    if svc is None:
        raise DispatchUnavailableError("Approval service is unavailable.")
    return cast(ApprovalService, svc)



def _bounded_message(lines: list[str], limit: int = 3500) -> str:
    result = "\n".join(lines)
    return result if len(result) <= limit else result[: limit - 1].rstrip() + "…"


async def _control_tower_error(message: object, action: str, error: Exception) -> None:
    if isinstance(error, (ValidationError, InvalidCategoryError)):
        text = f"{action}: check the supplied information and try again."
    elif isinstance(error, ControlTowerError):
        text = f"{action}: the request cannot be completed safely."
    else:
        LOGGER.exception("Unexpected Control Tower handler failure")
        text = f"{action}: temporary problem. Please try again later."
    await cast(object, message).reply_text(text)


async def capture_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    service = _control_tower(context)
    if message is None:
        return
    if service is None:
        await message.reply_text("Control Tower is temporarily unavailable.")
        return
    raw = _argument_text(context)
    fields = raw.split(maxsplit=1)
    if len(fields) != 2:
        await message.reply_text("Usage: /capture category title")
        return
    try:
        item = service.capture_work(fields[0], fields[1], "telegram")
    except Exception as error:
        await _control_tower_error(message, "Capture failed", error)
        return
    await message.reply_text(f"Captured: {item.title} ({item.item_id[:8]}). Route: {item.recommended_route}.")


async def today_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    service = _control_tower(context)
    if message is None:
        return
    if service is None:
        await message.reply_text("Control Tower is temporarily unavailable.")
        return
    try:
        items = service.get_today_priorities()
    except Exception as error:
        await _control_tower_error(message, "Today view failed", error)
        return
    lines = ["Today priorities:"] + ([f"• {item.title} — {item.status}" for item in items] or ["• No active priorities."])
    await message.reply_text(_bounded_message(lines))


async def approvals_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    service = _control_tower(context)
    if message is None:
        return
    if service is None:
        await message.reply_text("Control Tower is temporarily unavailable.")
        return
    try:
        approvals = service.list_approvals()
    except Exception as error:
        await _control_tower_error(message, "Approval inbox failed", error)
        return
    lines = ["Approval inbox:"] + ([f"• {item.source_system}: {item.requested_action}" for item in approvals] or ["• No pending approvals."])
    await message.reply_text(_bounded_message(lines))


async def shutdown_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    service = _control_tower(context)
    if message is None:
        return
    if service is None:
        await message.reply_text("Control Tower is temporarily unavailable.")
        return
    try:
        summary = service.evening_shutdown()
    except Exception as error:
        await _control_tower_error(message, "Shutdown summary failed", error)
        return
    lines = [
        "Evening shutdown:", f"• Completed today: {len(summary.completed_today)}",
        f"• In progress: {len(summary.in_progress)}", f"• Moved to tomorrow: {len(summary.moved_to_tomorrow)}",
        f"• Awaiting approval: {len(summary.awaiting_approval)}", f"• Clarification needed: {len(summary.unresolved_clarification)}",
        f"• Night Shift eligible: {summary.night_shift_eligibility.eligible_count}",
    ]
    lines.append("• No nighttime attention is required." if not summary.night_shift_required else "• Nighttime attention requires explicit approval.")
    await message.reply_text(_bounded_message(lines))


async def morning_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    service = _control_tower(context)
    if message is None:
        return
    if service is None:
        await message.reply_text("Control Tower is temporarily unavailable.")
        return
    try:
        brief = service.morning_brief()
    except Exception as error:
        await _control_tower_error(message, "Morning brief failed", error)
        return
    lines = ["Morning brief:"]
    lines.extend(f"• {item.title}" for item in brief.today_priorities)
    lines.append(f"• Pending approvals: {len(brief.approval_queue)}")
    lines.append(f"• Failed-safe items: {len(brief.failed_safe_items)}")
    lines.append(f"• Decisions needed: {len(brief.decisions_needed)}")
    if brief.latest_night_shift_brief is not None:
        lines.append("• Latest Night Shift morning brief is available.")
    await message.reply_text(_bounded_message(lines))



async def _dispatch_reply_error(message: Message, action: str, error: Exception) -> None:
    if isinstance(error, DispatchError):
        text = f"{action}: the request cannot be completed safely."
    else:
        LOGGER.error("Dispatch Telegram operation failed: %s", type(error).__name__)
        text = f"{action}: temporary problem. Please try again later."
    await message.reply_text(_bounded_message([text]))


async def _dispatch_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Message | None:
    if not await _require_authorized_user(update, context):
        return None
    return update.effective_message


async def handle_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _dispatch_message(update, context)
    if message is None:
        return
    parts = (message.text or "").split()
    if len(parts) not in {3, 4}:
        await message.reply_text("Usage: /dispatch <agent_id> <capability> [payload_ref]")
        return
    actor = f"user:{update.effective_user.id}" if update.effective_user else "user:unknown"
    try:
        request = DispatchRequest(
            source_type="telegram_direct", source_id=f"telegram:{message.message_id}",
            agent_id=parts[1], capability=parts[2], payload_ref=parts[3] if len(parts) == 4 else "",
            idempotency_key=f"telegram:{message.message_id}", requested_by=actor,
        )
        record = _dispatch_svc(context).create_dispatch(request, actor)
        await message.reply_text(_bounded_message([f"Dispatch {record.dispatch_id[:12]} created: {record.status}. "]))
    except Exception as error:
        await _dispatch_reply_error(message, "Dispatch creation failed", error)


async def handle_dispatches(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _dispatch_message(update, context)
    if message is None:
        return
    try:
        records = _dispatch_svc(context).list_dispatches(limit=20)
        if not records:
            await message.reply_text("No dispatches.")
            return
        lines = ["Recent dispatches:"]
        lines.extend(f"{item.dispatch_id[:12]} | {item.agent_id} | {item.capability} | {item.status}" for item in records)
        await message.reply_text(_bounded_message(lines))
    except Exception as error:
        await _dispatch_reply_error(message, "Dispatch listing failed", error)


async def handle_dispatchstatus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _dispatch_message(update, context)
    if message is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.reply_text("Usage: /dispatchstatus <dispatch_id>")
        return
    try:
        record = _dispatch_svc(context).get_dispatch(parts[1])
        await message.reply_text(_bounded_message([
            f"Dispatch: {record.dispatch_id[:12]}", f"Status: {record.status}",
            f"Agent: {record.agent_id}", f"Capability: {record.capability}",
        ]))
    except Exception as error:
        await _dispatch_reply_error(message, "Dispatch status failed", error)


async def handle_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _dispatch_message(update, context)
    if message is None:
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or update.effective_user is None:
        await message.reply_text("Usage: /approve <approval_id>")
        return
    try:
        decision = _approval_svc(context).approve(parts[1], update.effective_user.id)
        record = _dispatch_svc(context).dispatch(decision.dispatch_id, f"user:{update.effective_user.id}")
        await message.reply_text(_bounded_message([f"Approval recorded. Dispatch: {record.status}. "]))
    except Exception as error:
        await _dispatch_reply_error(message, "Approval failed", error)


async def handle_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _dispatch_message(update, context)
    if message is None:
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or update.effective_user is None:
        await message.reply_text("Usage: /reject <approval_id> [reason]")
        return
    try:
        _approval_svc(context).reject(parts[1], update.effective_user.id, parts[2] if len(parts) == 3 else "")
        await message.reply_text("Approval rejected.")
    except Exception as error:
        await _dispatch_reply_error(message, "Rejection failed", error)


async def handle_canceldispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _dispatch_message(update, context)
    if message is None:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or update.effective_user is None:
        await message.reply_text("Usage: /canceldispatch <dispatch_id>")
        return
    try:
        result = _dispatch_svc(context).cancel_dispatch(parts[1], f"user:{update.effective_user.id}", "Telegram cancellation")
        await message.reply_text(f"Dispatch cancelled: {result.dispatch_id[:12]}.")
    except Exception as error:
        await _dispatch_reply_error(message, "Cancellation failed", error)


async def handle_retrydispatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await _dispatch_message(update, context)
    if message is None:
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or update.effective_user is None:
        await message.reply_text("Usage: /retrydispatch <dispatch_id>")
        return
    try:
        record = _dispatch_svc(context).retry_dispatch(parts[1], f"user:{update.effective_user.id}")
        await message.reply_text(f"Dispatch retry completed: {record.status}.")
    except Exception as error:
        await _dispatch_reply_error(message, "Retry failed", error)



async def nightshift_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    if message:
        await message.reply_text("Night Shift Automation Runtime is operational.")

async def nightstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    if message:
        night_shift = context.application.bot_data.get("night_shift")
        if not night_shift:
             await message.reply_text("Night Shift is unavailable.")
             return
        mode = night_shift.repository.get_mode()
        await message.reply_text(f"Night Shift Mode: {mode.mode if mode else 'Unknown'}")

async def nightqueue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    if not message:
        return
    night_worker = context.application.bot_data.get("night_worker")
    if not night_worker:
        await message.reply_text("Night Worker is unavailable.")
        return

    args = context.args or []
    if args and args[0] == "cancel":
        if len(args) < 2:
            await message.reply_text("Usage: /nightqueue cancel <job_id>")
            return
        job_id = args[1]
        job = night_worker.repository.get_job_by_job_id(job_id)
        if job is None:
            await message.reply_text("No such Night Shift job.")
            return
        try:
            cancelled = night_worker.cancel_job(job, "cancelled via telegram", actor=f"user:{update.effective_user.id}")
        except Exception:
            await message.reply_text("That job could not be cancelled (it may already be terminal).")
            return
        await message.reply_text(f"Cancelled {cancelled.job_id} (status: {cancelled.status}).")
        return

    jobs = night_worker.repository.list_jobs(status="queued") + night_worker.repository.list_jobs(status="preparing")
    if not jobs:
        await message.reply_text("Night Shift Queue is empty.")
        return
    lines = [f"Night Shift Queue: {len(jobs)} in progress"]
    for j in jobs[:5]:
        lines.append(f"• {j.job_id}: {j.job_type} (attempt {j.attempt_count}, status {j.status})")
    await message.reply_text("\n".join(lines))

async def wake_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    if message:
        night_shift = context.application.bot_data.get("night_shift")
        if not night_shift:
             await message.reply_text("Night Shift is unavailable.")
             return
        night_shift.transition_runtime_mode("active", True, "telegram_user", "Manual wake up via Telegram")
        await message.reply_text("System mode set to active. Night Shift paused.")


_nightshift_tick_lock = threading.Lock()


async def _nightshift_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduler callback for `application.job_queue.run_repeating`.

    PTB (>=20) passes custom data via `Job.data`, retrieved here as
    `context.job.data` — not `context.job.context`, which does not exist on
    the installed python-telegram-bot version. A non-blocking lock skips an
    overlapping invocation (e.g. a slow tick still running when the next is
    due) rather than allowing two ticks to process the same jobs concurrently.
    """
    if not _nightshift_tick_lock.acquire(blocking=False):
        LOGGER.error("night_shift_tick event=skipped_overlap")
        return
    try:
        data = getattr(context.job, "data", None) or {}
        night_shift = data.get("night_shift")
        night_worker = data.get("night_worker")
        if night_shift is not None:
            try:
                night_shift.tick(datetime.now(UTC))
            except Exception:
                LOGGER.error("night_shift_tick event=tick_failed")
        if night_worker is None:
            return
        for job in night_worker.list_eligible_jobs():
            try:
                claimed = night_worker.claim_job(job)
                night_worker.execute_via_dispatch(claimed)
            except Exception:
                LOGGER.error("night_shift_tick event=job_isolated_failure job_id=%s", getattr(job, "job_id", "-"))
        for stale in night_worker.repository.list_jobs(status="preparing"):
            try:
                night_worker.recover_stale_job(stale)
            except Exception:
                LOGGER.error("night_shift_tick event=recovery_isolated_failure job_id=%s", getattr(stale, "job_id", "-"))
    finally:
        _nightshift_tick_lock.release()



def _resolve_chapter_order_token(service: DissertationService, token: str) -> int | None:
    """Resolve a chapter-order token ('-' for none) to a chapter id."""
    if token == "-":
        return None
    order_index = int(token)
    chapter = next((item for item in service.list_chapters() if item.order_index == order_index), None)
    if chapter is None:
        raise DissertationTargetNotFoundError("Chapter not found")
    return chapter.id


async def dissertation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Render dissertation workspace views and accept explicit, structured source/evidence writes."""
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    service = _dissertation(context)
    if message is None:
        return
    if service is None:
        await message.reply_text("Dissertation Workspace is temporarily unavailable.")
        return

    args = context.args
    view = args[0] if args else "overview"
    try:
        if view == "overview":
            overview = service.get_overview()
            lines = [
                f"Dissertation: {overview.workspace.title}",
                f"Status: {overview.workspace.status}",
                f"Progress: {overview.overall_progress:.0f}%",
                f"Focus: {overview.current_focus or '-'}",
                f"Chapters: {len(overview.chapters)} | Sources: {overview.total_sources} | Evidence: {overview.total_evidence}",
                f"Open gaps: {overview.open_gaps} | Pending tasks: {overview.pending_tasks}",
                f"Next: {overview.next_action}",
            ]
        elif view == "chapter" and len(args) == 2:
            order_index = int(args[1])
            chapter = next((item for item in service.list_chapters() if item.order_index == order_index), None)
            if chapter is None:
                raise ValueError("Chapter not found")
            detail = service.get_chapter_detail(chapter.id)
            lines = [
                f"Chapter {chapter.order_index}: {chapter.title}",
                f"Status: {chapter.status} | Progress: {detail.progress:.0f}%",
                f"Focus: {chapter.current_focus or '-'}",
                f"Sources: {len(detail.sources)} | Evidence: {len(detail.evidence)} | Open gaps: {detail.open_gaps}",
                f"Pending tasks: {detail.pending_tasks}",
                f"Next: {detail.next_action}",
            ]
        elif view == "gaps":
            gaps = [gap for gap in service.list_gaps() if gap.status != "resolved"]
            lines = ["Open dissertation gaps:"] + ([f"• {gap.description} — {gap.status}" for gap in gaps] or ["• None."])
        elif view == "next":
            lines = [f"Next academic action: {service.get_next_action()}"]
        elif view == "tasks":
            tasks = service.list_research_tasks()
            lines = ["Academic tasks:"] + ([f"• {item['work_item'].title} — {item['work_item'].status}" for item in tasks] or ["• None."])
        elif view == "evidence" and len(args) == 2:
            order_index = int(args[1])
            chapter = next((item for item in service.list_chapters() if item.order_index == order_index), None)
            if chapter is None:
                raise ValueError("Chapter not found")
            evidence = service.list_evidence(chapter_id=chapter.id)
            lines = [f"Evidence for chapter {order_index}:"] + (
                [f"• #{item.id} {item.summary} (source #{item.source_id})" for item in evidence] or ["• None."]
            )
        elif view == "sources" and len(args) in {1, 2}:
            chapter_id = None
            if len(args) == 2:
                order_index = int(args[1])
                chapter = next((item for item in service.list_chapters() if item.order_index == order_index), None)
                if chapter is None:
                    raise ValueError("Chapter not found")
                chapter_id = chapter.id
            sources = service.list_sources(chapter_id=chapter_id)
            lines = ["Dissertation sources:"] + (
                [f"• #{item.id} {item.title} — {item.status}" for item in sources] or ["• None."]
            )
        elif view == "decisions":
            decisions = service.list_decisions()
            lines = ["Dissertation decisions:"] + ([f"• {item['decision'].summary}" for item in decisions] or ["• None."])
        elif view == "addsource":
            actor = f"user:{update.effective_user.id}"
            fields = _pipe_fields(" ".join(args[1:]), 4, 5)
            if fields is None:
                lines = [
                    "Usage: /dissertation addsource <chapter n|-> | <title> | <source type> | <citation> | <locator>",
                    "Source types: book, journal_article, thesis, conference_paper, report, website, dataset, other",
                ]
            else:
                chapter_id = _resolve_chapter_order_token(service, fields[0])
                locator = fields[4] if len(fields) == 5 else None
                source = service.create_source(
                    fields[1], fields[2], fields[3], actor, locator=locator, chapter_id=chapter_id
                )
                lines = [f"Source created: #{source.id} {source.title} ({source.source_type}, {source.status})"]
        elif view == "addevidence":
            actor = f"user:{update.effective_user.id}"
            fields = _pipe_fields(" ".join(args[1:]), 3, 5)
            if fields is None:
                lines = [
                    "Usage: /dissertation addevidence <source id> | <chapter n|-> | <summary> | <locator> | <confidence>",
                    "Confidence: LOW, MEDIUM, HIGH (optional, default MEDIUM)",
                ]
            else:
                source_id = int(fields[0])
                chapter_id = _resolve_chapter_order_token(service, fields[1])
                locator_detail = fields[3] if len(fields) >= 4 else None
                confidence = fields[4].strip().upper() if len(fields) == 5 else "MEDIUM"
                evidence = service.create_evidence(
                    source_id,
                    fields[2],
                    actor,
                    chapter_id=chapter_id,
                    locator_detail=locator_detail,
                    confidence=confidence,
                )
                lines = [
                    f"Evidence created: #{evidence.id} for source #{evidence.source_id} "
                    f"(confidence: {evidence.confidence})"
                ]
        else:
            lines = [
                "Usage: /dissertation [chapter <n>|gaps|next|tasks|evidence <n>|sources [n]|decisions"
                "|addsource ...|addevidence ...]"
            ]
    except (InvalidDissertationValueError, DissertationTargetNotFoundError) as error:
        lines = [str(error)]
    except (ValueError, TypeError):
        lines = ["Invalid dissertation view or chapter number."]
    except Exception:
        LOGGER.exception("Dissertation handler failed")
        lines = ["Dissertation Workspace is temporarily unavailable."]
    await message.reply_text(_bounded_message(lines))


async def knowledgesource_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    service = _knowledge(context)
    if message is None:
        return
    if service is None:
        await message.reply_text("Knowledge Operations is temporarily unavailable.")
        return
    fields = _pipe_fields(" ".join(context.args), 3, 4)
    if fields is None:
        await message.reply_text("Usage: /knowledgesource <title> | <source type> | <citation> [| <locator>]")
        return
    try:
        source = service.create_source(
            fields[0], fields[1], fields[2], f"user:{update.effective_user.id}",
            origin_system="telegram", origin_ref=fields[3] if len(fields) == 4 else None,
        )
        await message.reply_text(f"Knowledge source created: #{source.id} {source.title} ({source.source_type})")
    except (InvalidKnowledgeValueError, KnowledgeError) as error:
        await message.reply_text(str(error))
    except Exception:
        LOGGER.exception("Knowledge source handler failed")
        await message.reply_text("Knowledge Operations is temporarily unavailable.")


async def knowledgeitem_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    service = _knowledge(context)
    if message is None:
        return
    if service is None:
        await message.reply_text("Knowledge Operations is temporarily unavailable.")
        return
    fields = _pipe_fields(" ".join(context.args), 3, 5)
    if fields is None:
        await message.reply_text("Usage: /knowledgeitem <source id> | <title> | <summary> [| <tags>] [| <confidence>]")
        return
    try:
        item = service.create_item(
            int(fields[0]), fields[1], fields[2], f"user:{update.effective_user.id}",
            tags=fields[3] if len(fields) >= 4 else "",
            confidence=fields[4] if len(fields) == 5 else "MEDIUM",
        )
        await message.reply_text(
            f"Knowledge item created: #{item.id} from source #{item.source_id} (confidence: {item.confidence})"
        )
    except (InvalidKnowledgeValueError, KnowledgeError) as error:
        await message.reply_text(str(error))
    except (ValueError, TypeError):
        await message.reply_text("Source id must be a positive integer.")
    except Exception:
        LOGGER.exception("Knowledge item handler failed")
        await message.reply_text("Knowledge Operations is temporarily unavailable.")


async def knowledgequery_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized_user(update, context):
        return
    message = update.effective_message
    service = _knowledge(context)
    if message is None:
        return
    if service is None:
        await message.reply_text("Knowledge Operations is temporarily unavailable.")
        return
    query = " ".join(context.args)
    if not query.strip():
        await message.reply_text("Usage: /knowledgequery <keyword or tag>")
        return
    try:
        results = service.query(keyword=query)
        lines = ["Knowledge results:"] + (
            [
                f"• #{result.item.id} {result.item.title} — {result.item.summary}\n"
                f"  Source #{result.source.id}: {result.source.title} ({result.source.source_type})\n"
                f"  Citation: {result.source.citation_text}"
                for result in results
            ] or ["• None."]
        )
        await message.reply_text(_bounded_message(lines))
    except InvalidKnowledgeValueError as error:
        await message.reply_text(str(error))
    except Exception:
        LOGGER.exception("Knowledge query handler failed")
        await message.reply_text("Knowledge Operations is temporarily unavailable.")


def build_application(
    settings: Settings,
    memory: WorkspaceMemoryService,
    execution: ExecutionService | None = None,
    provider: ProviderGatewayService | None = None,
    night_shift: NightShiftService | None = None,
    control_tower: ControlTowerService | None = None,
    dispatch: DispatchService | None = None,
    approvals: ApprovalService | None = None,
    night_worker: NightShiftWorker | None = None,
    dissertation: DissertationService | None = None,
    knowledge: KnowledgeService | None = None,
) -> Application:
    """Build the local polling application with scoped command handlers."""
    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.bot_data["settings"] = settings
    application.bot_data["memory"] = memory
    application.bot_data["execution"] = execution
    application.bot_data["night_shift"] = night_shift
    application.bot_data["control_tower"] = control_tower
    application.bot_data["dissertation"] = dissertation
    application.bot_data["knowledge"] = knowledge
    if dispatch is not None:
        application.bot_data["dispatch_svc"] = dispatch
    if approvals is not None:
        application.bot_data["approval_svc"] = approvals
    if night_worker:
        application.bot_data["night_worker"] = night_worker
    application.bot_data["provider"] = provider
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
    application.add_handler(CommandHandler("ask", ask_command))
    application.add_handler(CommandHandler("providerstatus", providerstatus_command))

    application.add_handler(CommandHandler("capture", capture_handler))
    application.add_handler(CommandHandler("today", today_handler))
    application.add_handler(CommandHandler("approvals", approvals_handler))
    application.add_handler(CommandHandler("shutdown", shutdown_handler))
    application.add_handler(CommandHandler("morning", morning_handler))
    application.add_handler(CommandHandler("nightshift", nightshift_command))
    application.add_handler(CommandHandler("nightstatus", nightstatus_command))
    application.add_handler(CommandHandler("nightqueue", nightqueue_command))
    application.add_handler(CommandHandler("wake", wake_command))
    application.add_handler(CommandHandler("dissertation", dissertation_handler))
    application.add_handler(CommandHandler("knowledgesource", knowledgesource_handler))
    application.add_handler(CommandHandler("knowledgeitem", knowledgeitem_handler))
    application.add_handler(CommandHandler("knowledgequery", knowledgequery_handler))


    if dispatch is not None:
        application.add_handler(CommandHandler("dispatch", handle_dispatch))
        application.add_handler(CommandHandler("dispatches", handle_dispatches))
        application.add_handler(CommandHandler("dispatchstatus", handle_dispatchstatus))
        application.add_handler(CommandHandler("approve", handle_approve))
        application.add_handler(CommandHandler("reject", handle_reject))
        application.add_handler(CommandHandler("canceldispatch", handle_canceldispatch))
        application.add_handler(CommandHandler("retrydispatch", handle_retrydispatch))

    # Generic text fallback must be registered after every command handler.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(handle_error)

    # Register the Night Shift automation tick exactly once per build_application()
    # call. Missing JobQueue support (e.g. python-telegram-bot installed without
    # the [job-queue] extra) fails safe: no automation runs, nothing else breaks.
    if getattr(application, "job_queue", None) is not None:
        application.job_queue.run_repeating(
            _nightshift_tick,
            interval=300,
            first=10,
            data={"night_shift": night_shift, "night_worker": night_worker},
        )

    return application
