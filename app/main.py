"""Local NOVA Telegram bot entry point."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

from app.config import ConfigurationError, load_settings
from app.dissertation.service import DissertationService
from app.knowledge.service import KnowledgeService
from app.memory import MemoryDatabase, MemoryDatabaseError, WorkspaceMemoryService
from app.execution.service import ExecutionService
from app.nightshift import NightShiftService
from app.control_tower.service import ControlTowerService
from app.brief.service import ExecutiveBriefService

from app.dispatch.registry import AgentRegistry
from app.dispatch.approvals import ApprovalService
from app.dispatch.service import DispatchService
from app.dispatch.adapters import ProviderGatewayAgentAdapter
from app.agent_assignment.service import AgentAssignmentService
from app.nightshift.worker import NightShiftWorker

from app.providers.errors import ConfigurationError as ProviderConfigurationError
from app.providers.ninerouter import NineRouterAdapter
from app.providers.specialists.codex_adapter import CodexAdapter
from app.providers.specialists.claude_adapter import ClaudeAdapter
from app.providers.adapter import ProviderAdapter
from app.providers.repository import ProviderRepository
from app.providers.service import ProviderGatewayService
from app.conversation import ConversationService
from app.drafting import DraftingService
from app.google_workspace.bundle import WorkspaceConnectorBundle
from app.intake import IntakeService
from app.telegram_bot import build_application
from app.workspace_bridge import WorkspaceBridgeService
from app.workspace_intel import WorkspaceIntelService


class _UnavailableWorkspaceAuthenticator:
    """Fail closed when Workspace OAuth has not been configured."""

    def get_account_namespace(self) -> None:
        return None


def configure_logging() -> None:
    """Configure minimal local operational logging without sensitive payloads."""
    log_format = "%(asctime)s %(levelname)s %(name)s: %(message)s"

    repo_root = Path(__file__).resolve().parent.parent
    data_dir = repo_root / "data"
    data_dir.mkdir(exist_ok=True)
    log_file = data_dir / "nova.log"

    handlers: list[logging.Handler] = [logging.StreamHandler()]

    # 5MB max bytes, 3 backups -> 20MB total max bounded log
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8"
    )
    handlers.append(file_handler)

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


def main() -> int:
    """Validate configuration and start the local polling bot."""
    configure_logging()
    logger = logging.getLogger(__name__)

    try:
        settings = load_settings()
    except ConfigurationError as error:
        logger.error("Startup configuration error: %s", error)
        return 1

    logger.info("Starting NOVA local Telegram bot.")
    memory = WorkspaceMemoryService(MemoryDatabase(settings.nova_memory_db_path))
    try:
        memory.initialize()
    except MemoryDatabaseError:
        logger.error("Workspace Memory initialization failed.")
        return 1

    execution_svc = ExecutionService(MemoryDatabase(settings.nova_memory_db_path), settings.telegram_allowed_user_id)
    try:
        execution_svc.initialize()
    except MemoryDatabaseError:
        logger.error("Execution schema initialization failed.")
        return 1

    night_shift = NightShiftService(MemoryDatabase(settings.nova_memory_db_path))
    try:
        night_shift.initialize()
    except MemoryDatabaseError:
        logger.error("Night Shift Runtime schema initialization failed.")
        return 1

    registry_svc = AgentRegistry()
    approval_svc = ApprovalService(MemoryDatabase(settings.nova_memory_db_path), authorized_user_id=settings.telegram_allowed_user_id)
    try:
        approval_svc.initialize()
    except MemoryDatabaseError:
        logger.error("Approval Service schema initialization failed.")
        return 1

    dispatch_svc = DispatchService(MemoryDatabase(settings.nova_memory_db_path), registry=registry_svc, approvals=approval_svc)
    try:
        dispatch_svc.initialize()
    except MemoryDatabaseError:
        logger.error("Dispatch Service schema initialization failed.")
        return 1

    agent_assignments = AgentAssignmentService(
        MemoryDatabase(settings.nova_memory_db_path),
        registry=registry_svc,
        dispatch=dispatch_svc,
        approvals=approval_svc,
    )
    try:
        agent_assignments.initialize()
    except MemoryDatabaseError:
        logger.error("Agent Assignment schema initialization failed.")
        return 1

    night_worker = NightShiftWorker(
        service=night_shift,
        dispatch_service=dispatch_svc,
        approval_service=approval_svc,
        agent_registry=registry_svc
    )

    control_tower = ControlTowerService(
        MemoryDatabase(settings.nova_memory_db_path),
        execution=execution_svc,
        night_shift=night_shift,
        approvals=approval_svc,
        agent_assignments=agent_assignments,
    )
    try:
        control_tower.initialize()
    except MemoryDatabaseError:
        logger.error("Control Tower schema initialization failed.")
        return 1

    dissertation = DissertationService(MemoryDatabase(settings.nova_memory_db_path), control_tower=control_tower)
    try:
        dissertation.initialize()
    except MemoryDatabaseError:
        logger.error("Dissertation Workspace initialization failed.")
        return 1

    knowledge = KnowledgeService(
        MemoryDatabase(settings.nova_memory_db_path), memory=memory, control_tower=control_tower
    )
    try:
        knowledge.initialize()
    except MemoryDatabaseError:
        logger.error("Knowledge Operations schema initialization failed.")
        return 1

    # Workspace OAuth remains optional and must not be probed at startup. The
    # Stage-1 status handler accepts a missing bundle and reports it safely.
    google_workspace: WorkspaceConnectorBundle | None = None

    drafting = DraftingService(memory.database)
    try:
        drafting.initialize()
    except MemoryDatabaseError:
        logger.error("Drafting schema initialization failed.")
        return 1

    workspace_bridge = WorkspaceBridgeService(
        memory.database,
        google_workspace.authenticator if google_workspace is not None else _UnavailableWorkspaceAuthenticator(),
        control_tower,
        knowledge,
    )
    try:
        workspace_bridge.initialize()
    except MemoryDatabaseError:
        logger.error("Workspace bridge schema initialization failed.")
        return 1

    executive_brief = ExecutiveBriefService(control_tower, night_shift=night_shift, knowledge=knowledge)

    workspace_intel = (
        WorkspaceIntelService(google_workspace.gmail, google_workspace.calendar)
        if google_workspace is not None
        else None
    )

    intake = IntakeService(
        memory.database,
        memory=memory,
        control_tower=control_tower,
        knowledge=knowledge,
    )
    try:
        intake.initialize()
    except MemoryDatabaseError:
        logger.error("External message intake schema initialization failed.")
        return 1

    conversation = ConversationService(memory.database)
    try:
        conversation.initialize()
    except MemoryDatabaseError:
        logger.error("Conversation schema initialization failed.")
        return 1

    provider_svc: ProviderGatewayService | None = None

    if settings.nova_provider_base_url and settings.nova_provider_api_key:
        adapters: dict[str, ProviderAdapter] = {
            "9Router": NineRouterAdapter(settings.nova_provider_base_url, settings.nova_provider_api_key),
        }
        if settings.nova_codex_executable_path:
            adapters["Codex"] = CodexAdapter(settings.nova_codex_executable_path)
        if settings.nova_claude_executable_path:
            adapters["Claude"] = ClaudeAdapter(settings.nova_claude_executable_path)
        provider_svc = ProviderGatewayService(
            ProviderRepository(MemoryDatabase(settings.nova_memory_db_path)),
            adapters,
            settings.nova_provider_base_url,
            settings.nova_provider_api_key,
            settings.nova_provider_model_priority,
            settings.nova_provider_allowed_models,
            combo_priorities={
                "coding": settings.nova_provider_coding_combo_priority or ["nova-v1-coding", "nova-v1-coding-fallback"],
                "review": settings.nova_provider_review_combo_priority or ["nova-v1-review", "nova-v1-review-fallback"],
            },
            upstream_route_overrides=settings.nova_provider_upstream_route_map,
        )
        try:
            provider_svc.initialize()
            ProviderGatewayAgentAdapter.set_service_factory(lambda: provider_svc)
        except (MemoryDatabaseError, ProviderConfigurationError):
            logger.error("Provider Gateway initialization failed; continuing without it.")
            provider_svc = None
            ProviderGatewayAgentAdapter.set_service_factory(lambda: None)

    application = build_application(
        settings,
        memory,
        execution_svc,
        provider_svc,
        night_shift,
        control_tower,
        dispatch_svc,
        approval_svc,
        night_worker,
        dissertation,
        agent_assignments,
        knowledge,
        executive_brief,
        google_workspace=google_workspace,
        workspace_intel=workspace_intel,
        intake=intake,
        conversation=conversation,
        drafting=drafting,
        workspace_bridge=workspace_bridge,
    )
    application.run_polling()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
