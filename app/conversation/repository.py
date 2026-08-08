"""SQLite persistence and compare-and-swap transitions for conversations."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.conversation.models import Choice, PendingInteraction
from app.memory.database import MemoryDatabase


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationRepository:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    @staticmethod
    def _interaction(row: sqlite3.Row) -> PendingInteraction:
        choices = tuple(Choice(**choice) for choice in json.loads(row["choices_json"]))
        return PendingInteraction(
            interaction_id=row["interaction_id"],
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            source_command=row["source_command"],
            prompt_summary=row["prompt_summary"],
            choices=choices,
            max_risk_level=row["max_risk_level"],
            status=row["status"],
            resolved_choice_index=row["resolved_choice_index"],
            resolved_at=row["resolved_at"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def insert_in(self, connection: sqlite3.Connection, interaction: PendingInteraction, actor: str) -> None:
        connection.execute(
            """INSERT INTO conversation_pending_interactions (
                interaction_id, chat_id, user_id, source_command, prompt_summary, choices_json,
                max_risk_level, status, resolved_choice_index, resolved_at, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                interaction.interaction_id,
                interaction.chat_id,
                interaction.user_id,
                interaction.source_command,
                interaction.prompt_summary,
                json.dumps([choice.__dict__ for choice in interaction.choices], sort_keys=True),
                interaction.max_risk_level,
                interaction.status,
                interaction.resolved_choice_index,
                interaction.resolved_at,
                interaction.created_at,
                interaction.expires_at,
            ),
        )
        self.audit_in(connection, interaction.interaction_id, "asked", actor)

    def get_open_for_chat_in(self, connection: sqlite3.Connection, chat_id: str) -> PendingInteraction | None:
        row = connection.execute(
            "SELECT * FROM conversation_pending_interactions WHERE chat_id = ? AND status = 'open'",
            (chat_id,),
        ).fetchone()
        return self._interaction(row) if row is not None else None

    def get_open_for_user_in(self, connection: sqlite3.Connection, user_id: int) -> PendingInteraction | None:
        row = connection.execute(
            """SELECT * FROM conversation_pending_interactions
               WHERE user_id = ? AND status = 'open'
               ORDER BY created_at DESC, interaction_id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        return self._interaction(row) if row is not None else None

    def transition_in(
        self,
        connection: sqlite3.Connection,
        interaction_id: str,
        target_status: str,
        actor: str,
        *,
        resolved_choice_index: int | None = None,
        event: str,
        detail: str = "",
    ) -> bool:
        resolved_at = utc_now() if target_status == "resolved" else None
        updated = connection.execute(
            """UPDATE conversation_pending_interactions
               SET status = ?, resolved_choice_index = ?, resolved_at = ?
               WHERE interaction_id = ? AND status = 'open'""",
            (target_status, resolved_choice_index, resolved_at, interaction_id),
        ).rowcount == 1
        if updated:
            self.audit_in(connection, interaction_id, event, actor, detail)
        return updated

    def expire_open_in(self, connection: sqlite3.Connection, now: str) -> int:
        rows = connection.execute(
            "SELECT interaction_id FROM conversation_pending_interactions "
            "WHERE status = 'open' AND expires_at <= ?",
            (now,),
        ).fetchall()
        for row in rows:
            self.transition_in(
                connection,
                row["interaction_id"],
                "expired",
                "system:expiry",
                event="expired",
            )
        return len(rows)

    def audit_in(
        self,
        connection: sqlite3.Connection,
        interaction_id: str,
        event: str,
        actor: str,
        detail: str = "",
    ) -> None:
        connection.execute(
            """INSERT INTO conversation_audit_log (
                interaction_id, event, actor, detail, created_at
            ) VALUES (?, ?, ?, ?, ?)""",
            (interaction_id, event, actor, detail, utc_now()),
        )
