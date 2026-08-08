"""SQLite persistence owned solely by the intake domain."""

from __future__ import annotations

import sqlite3

from app.intake.models import ExternalMessageIntake
from app.memory.database import MemoryDatabase


class IntakeNotFoundError(LookupError):
    """Raised when the requested intake row does not exist."""


def _row_to_intake(row: sqlite3.Row) -> ExternalMessageIntake:
    return ExternalMessageIntake(
        id=row["id"],
        source_channel=row["source_channel"],
        telegram_update_id=row["telegram_update_id"],
        raw_text=row["raw_text"],
        classification=row["classification"],
        status=row["status"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        content_fingerprint=row["content_fingerprint"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class IntakeRepository:
    """Keep intake identity, status, and audit history in the intake tables."""

    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def get(self, intake_id: int) -> ExternalMessageIntake:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM external_message_intake WHERE id = ?", (intake_id,)
            ).fetchone()
        if row is None:
            raise IntakeNotFoundError("External message intake was not found")
        return _row_to_intake(row)

    def find_by_telegram_update_id(self, telegram_update_id: str) -> ExternalMessageIntake | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM external_message_intake WHERE telegram_update_id = ?", (telegram_update_id,)
            ).fetchone()
        return _row_to_intake(row) if row is not None else None

    def find_recent_pending_by_fingerprint(self, fingerprint: str, since: str) -> ExternalMessageIntake | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM external_message_intake
                WHERE content_fingerprint = ? AND status = 'pending_review' AND created_at >= ?
                ORDER BY id DESC LIMIT 1
                """,
                (fingerprint, since),
            ).fetchone()
        return _row_to_intake(row) if row is not None else None

    def create(
        self,
        *,
        source_channel: str,
        telegram_update_id: str | None,
        raw_text: str,
        classification: str,
        content_fingerprint: str,
        created_by: str,
        created_at: str,
    ) -> ExternalMessageIntake:
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO external_message_intake (
                    source_channel, telegram_update_id, raw_text, classification,
                    content_fingerprint, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source_channel, telegram_update_id, raw_text, classification, content_fingerprint,
                 created_by, created_at, created_at),
            )
            intake_id = int(cursor.lastrowid)
            self._audit(connection, intake_id, "captured", created_by, "")
        return self.get(intake_id)

    def resolve(self, intake_id: int, *, target_type: str, target_id: str, actor: str, updated_at: str) -> ExternalMessageIntake:
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE external_message_intake
                SET status = 'confirmed', target_type = ?, target_id = ?, updated_at = ?
                WHERE id = ? AND status = 'pending_review'
                """,
                (target_type, target_id, updated_at, intake_id),
            )
            if cursor.rowcount != 1:
                raise IntakeNotFoundError("External message intake is no longer pending")
            self._audit(connection, intake_id, "confirmed", actor, target_type)
        return self.get(intake_id)

    def dismiss(self, intake_id: int, *, actor: str, reason: str, updated_at: str) -> ExternalMessageIntake:
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE external_message_intake SET status = 'dismissed', updated_at = ?
                WHERE id = ? AND status = 'pending_review'
                """,
                (updated_at, intake_id),
            )
            if cursor.rowcount != 1:
                raise IntakeNotFoundError("External message intake is no longer pending")
            self._audit(connection, intake_id, "dismissed", actor, reason)
        return self.get(intake_id)

    def count(self) -> int:
        with self.database.connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM external_message_intake").fetchone()[0])

    @staticmethod
    def _audit(connection: sqlite3.Connection, intake_id: int, event: str, actor: str, detail: str) -> None:
        connection.execute(
            "INSERT INTO intake_audit_log (intake_id, event, actor, detail, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
            (intake_id, event, actor, detail),
        )
