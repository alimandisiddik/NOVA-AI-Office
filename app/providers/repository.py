"""Database repository for provider audit logging."""

from __future__ import annotations
import sqlite3
import hashlib
from datetime import UTC, datetime

from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.providers.schema import PROVIDER_SCHEMA
from app.providers.models import ProviderAuditRecord


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ProviderRepository:
    """Handles provider audit metadata persistence."""

    def __init__(self, database: MemoryDatabase) -> None:
        self._db = database

    def initialize(self) -> None:
        try:
            with self._db.connection() as conn:
                conn.executescript(PROVIDER_SCHEMA)
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Provider schema initialization failed") from exc

    def log_request(self, record: ProviderAuditRecord) -> None:
        try:
            with self._db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO provider_request_audit
                    (request_id, execution_id, user_id, provider_id, model_id,
                     workflow_id, role_id, status, prompt_hash, response_size,
                     latency_ms, retry_count, error_category, created_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.request_id,
                        record.execution_id,
                        record.user_id,
                        record.provider_id,
                        record.model_id,
                        record.workflow_id,
                        record.role_id,
                        record.status,
                        record.prompt_hash,
                        record.response_size,
                        record.latency_ms,
                        record.retry_count,
                        record.error_category,
                        record.created_at,
                        record.completed_at,
                    )
                )
        except sqlite3.IntegrityError as exc:
            # duplicate request ID rejection
            raise MemoryDatabaseError("Duplicate request_id rejected") from exc
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Failed to log provider request") from exc
