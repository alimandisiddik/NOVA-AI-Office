"""SQLite repository with compare-and-swap state transitions."""

from __future__ import annotations

import sqlite3

from app.workspace_actions.models import WorkspaceAction


class WorkspaceActionRepository:
    def __init__(self, database) -> None:
        self.database = database

    @staticmethod
    def _action(row: sqlite3.Row) -> WorkspaceAction:
        return WorkspaceAction(**dict(row))

    def get(self, action_id: int) -> WorkspaceAction | None:
        with self.database.connection() as connection:
            return self.get_in(connection, action_id)

    def get_in(self, connection: sqlite3.Connection, action_id: int) -> WorkspaceAction | None:
        row = connection.execute("SELECT * FROM workspace_actions WHERE id = ?", (action_id,)).fetchone()
        return self._action(row) if row else None

    def get_by_idempotency(self, key: str) -> WorkspaceAction | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM workspace_actions WHERE idempotency_key = ?", (key,)).fetchone()
            return self._action(row) if row else None

    def get_by_approval(self, approval_id: str) -> WorkspaceAction | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM workspace_actions WHERE approval_id = ?", (approval_id,)).fetchone()
            return self._action(row) if row else None

    def insert_in(self, connection: sqlite3.Connection, values: dict[str, object]) -> WorkspaceAction:
        fields = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = connection.execute(f"INSERT INTO workspace_actions ({fields}) VALUES ({placeholders})", tuple(values.values()))
        action = self.get_in(connection, int(cursor.lastrowid))
        assert action is not None
        return action

    def cas_in(self, connection: sqlite3.Connection, action: WorkspaceAction, expected: str, target: str, now: str, **values: object) -> bool:
        assignments = ["status = ?", "version = version + 1", "updated_at = ?"]
        params: list[object] = [target, now]
        for column, value in values.items():
            assignments.append(f"{column} = ?")
            params.append(value)
        params.extend((action.id, action.version, expected))
        return connection.execute(
            f"UPDATE workspace_actions SET {', '.join(assignments)} WHERE id = ? AND version = ? AND status = ?", params
        ).rowcount == 1

    @staticmethod
    def audit_in(connection: sqlite3.Connection, action: WorkspaceAction, event: str, actor: str, now: str, detail: str = "") -> None:
        connection.execute(
            "INSERT INTO workspace_action_audit_log (action_id,event,actor,correlation_id,detail,created_at) VALUES (?,?,?,?,?,?)",
            (action.id, event, actor, action.correlation_id, detail, now),
        )
