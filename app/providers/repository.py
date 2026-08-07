"""Database repository for provider audit logging."""

from __future__ import annotations
import sqlite3
import hashlib
from datetime import UTC, datetime
from typing import Optional

from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.providers.schema import PROVIDER_SCHEMA, MIGRATIONS
from app.providers.models import ProviderAuditRecord, ProviderRequestAttempt


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
                for migration in MIGRATIONS:
                    try:
                        conn.execute(migration)
                    except sqlite3.OperationalError:
                        # Ignore if column already exists
                        pass
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Provider schema initialization failed") from exc

    def log_request(self, record: ProviderAuditRecord, attempts: list[ProviderRequestAttempt] = None) -> None:
        if attempts is None:
            attempts = []
        try:
            with self._db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO provider_request_audit
                    (request_id, execution_id, user_id, provider_id, model_id,
                     workflow_id, role_id, status, prompt_hash, response_size,
                     latency_ms, retry_count, error_category, created_at, completed_at,
                     initial_model_id, final_model_id, attempt_count, fallback_used, fallback_reason,
                     initial_provider_id, final_provider_id, resolved_model_label,
                     initial_upstream_route_id, final_upstream_route_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        record.initial_model_id,
                        record.final_model_id,
                        record.attempt_count,
                        record.fallback_used,
                        record.fallback_reason,
                        record.initial_provider_id,
                        record.final_provider_id,
                        record.resolved_model_label,
                        record.initial_upstream_route_id,
                        record.final_upstream_route_id,
                    )
                )

                for attempt in attempts:
                    conn.execute(
                        """
                        INSERT INTO provider_request_attempts
                        (request_id, attempt_number, model_id, latency_ms, error_category, status, created_at, provider_id, upstream_route_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.request_id,
                            attempt.attempt_number,
                            attempt.model_id,
                            attempt.latency_ms,
                            attempt.error_category,
                            attempt.status,
                            attempt.created_at,
                            attempt.provider_id,
                            attempt.upstream_route_id,
                        )
                    )
        except sqlite3.IntegrityError as exc:
            if "provider_request_attempts.request_id, provider_request_attempts.attempt_number" in str(exc):
                raise MemoryDatabaseError("Duplicate attempt rejected") from exc
            if "provider_request_audit.request_id" in str(exc):
                raise MemoryDatabaseError("Duplicate request_id rejected") from exc
            raise MemoryDatabaseError("Provider audit integrity error") from exc
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Failed to log provider request") from exc
