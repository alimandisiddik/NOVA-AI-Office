"""Local NOVA Telegram bot entry point."""

from __future__ import annotations

import logging

from app.config import ConfigurationError, load_settings
from app.memory import MemoryDatabase, MemoryDatabaseError, WorkspaceMemoryService
from app.execution.service import ExecutionService
from app.telegram_bot import build_application


def configure_logging() -> None:
    """Configure minimal local operational logging without sensitive payloads."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
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

    application = build_application(settings, memory, execution_svc)
    application.run_polling()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
