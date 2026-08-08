"""SQLite persistence for local prepared Workspace actions."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.drafting.models import PreparedWorkspaceAction
from app.memory.database import MemoryDatabase


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DraftingRepository:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    @staticmethod
    def _action(row: sqlite3.Row) -> PreparedWorkspaceAction:
        return PreparedWorkspaceAction(**dict(row))

    def get_action(self, action_id: int) -> PreparedWorkspaceAction | None:
        with self.database.connection() as connection:
            return self.get_action_in(connection, action_id)

    def get_action_in(self, connection: sqlite3.Connection, action_id: int) -> PreparedWorkspaceAction | None:
        row = connection.execute(
            "SELECT * FROM prepared_workspace_actions WHERE id = ?", (action_id,)
        ).fetchone()
        return self._action(row) if row is not None else None

    def insert_action_in(
        self, connection: sqlite3.Connection, action: PreparedWorkspaceAction, *, event: str, detail: str = ""
    ) -> PreparedWorkspaceAction:
        cursor = connection.execute(
            """
            INSERT INTO prepared_workspace_actions (
                content_type, source_ref, title, body_text, body_payload, status,
                supersedes_id, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action.content_type,
                action.source_ref,
                action.title,
                action.body_text,
                action.body_payload,
                action.status,
                action.supersedes_id,
                action.created_by,
                action.created_at,
                action.updated_at,
            ),
        )
        action_id = int(cursor.lastrowid)
        self.audit_in(connection, action_id, event, action.created_by, detail)
        inserted = self.get_action_in(connection, action_id)
        assert inserted is not None
        return inserted

    def transition_in(
        self,
        connection: sqlite3.Connection,
        action_id: int,
        expected_status: str,
        next_status: str,
        actor: str,
        *,
        event: str,
        detail: str = "",
    ) -> bool:
        updated_at = utc_now()
        cursor = connection.execute(
            """
            UPDATE prepared_workspace_actions
            SET status = ?, updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (next_status, updated_at, action_id, expected_status),
        )
        if cursor.rowcount != 1:
            return False
        self.audit_in(connection, action_id, event, actor, detail)
        return True

    def list_actions(self, *, limit: int = 20) -> list[PreparedWorkspaceAction]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM prepared_workspace_actions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._action(row) for row in rows]

    @staticmethod
    def audit_in(
        connection: sqlite3.Connection, action_id: int, event: str, actor: str, detail: str = ""
    ) -> None:
        connection.execute(
            """
            INSERT INTO drafting_audit_log (action_id, event, actor, detail, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (action_id, event, actor, detail, utc_now()),
        )
