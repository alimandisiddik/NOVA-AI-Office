"""Local NOVA Telegram bot entry point."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os

from app.config import ConfigurationError, load_settings
from app.memory import MemoryDatabase, MemoryDatabaseError, WorkspaceMemoryService
from app.execution.service import ExecutionService
from app.providers.errors import ConfigurationError as ProviderConfigurationError
from app.providers.ninerouter import NineRouterAdapter
from app.providers.repository import ProviderRepository
from app.providers.service import ProviderGatewayService
from app.telegram_bot import build_application


def configure_logging() -> None:
    """Configure minimal local operational logging without sensitive payloads."""
    from pathlib import Path

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

    provider_svc: ProviderGatewayService | None = None
    if settings.nova_provider_base_url and settings.nova_provider_api_key:
        adapter = NineRouterAdapter(settings.nova_provider_base_url, settings.nova_provider_api_key)
        provider_svc = ProviderGatewayService(
            ProviderRepository(MemoryDatabase(settings.nova_memory_db_path)),
            adapter,
            settings.nova_provider_base_url,
            settings.nova_provider_api_key,
            settings.nova_provider_default_model,
            settings.nova_provider_allowed_models,
        )
        try:
            provider_svc.initialize()
        except (MemoryDatabaseError, ProviderConfigurationError):
            logger.error("Provider Gateway initialization failed; continuing without it.")
            provider_svc = None

    application = build_application(settings, memory, execution_svc, provider_svc)
    application.run_polling()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
