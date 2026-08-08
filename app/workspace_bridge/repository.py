"""SQLite persistence for Workspace bridge candidates only."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.memory.database import MemoryDatabase
from app.workspace_bridge.models import WorkspaceSourceRef


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceSourceRefNotFoundError(RuntimeError):
    """Raised when a requested bridge reference does not exist."""


class WorkspaceBridgeRepository:
    """Own only the bridge tables; canonical domain writes stay in services."""

    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def create_or_get(
        self,
        *,
        source_system: str,
        account_namespace: str,
        external_source_type: str,
        external_source_id: str,
        content_fingerprint: str,
        candidate_summary: str,
        actor: str,
    ) -> WorkspaceSourceRef:
        with self.database.connection() as connection:
            existing = connection.execute(
                """SELECT * FROM workspace_source_refs
                WHERE source_system = ? AND account_namespace = ?
                  AND external_source_type = ? AND external_source_id = ?""",
                (source_system, account_namespace, external_source_type, external_source_id),
            ).fetchone()
            if existing is not None:
                return _ref(existing)
            now = utc_now()
            cursor = connection.execute(
                """INSERT INTO workspace_source_refs
                (source_system, account_namespace, external_source_type, external_source_id,
                 content_fingerprint, candidate_summary, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_system,
                    account_namespace,
                    external_source_type,
                    external_source_id,
                    content_fingerprint,
                    candidate_summary,
                    actor,
                    now,
                    now,
                ),
            )
            ref_id = int(cursor.lastrowid)
            self._audit(connection, ref_id, "proposed", actor)
            row = connection.execute("SELECT * FROM workspace_source_refs WHERE id = ?", (ref_id,)).fetchone()
        return _ref(row)

    def get(self, ref_id: int) -> WorkspaceSourceRef | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM workspace_source_refs WHERE id = ?", (ref_id,)).fetchone()
        return None if row is None else _ref(row)

    def list_candidates(self) -> list[WorkspaceSourceRef]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM workspace_source_refs WHERE status = 'candidate' ORDER BY created_at, id"
            ).fetchall()
        return [_ref(row) for row in rows]

    def mark_committed(self, ref_id: int, target_type: str, target_id: str, actor: str) -> WorkspaceSourceRef:
        return self._transition(ref_id, "committed", actor, target_type=target_type, target_id=target_id)

    def mark_dismissed(self, ref_id: int, actor: str, reason: str) -> WorkspaceSourceRef:
        return self._transition(ref_id, "dismissed", actor, detail=reason)

    def _transition(
        self,
        ref_id: int,
        status: str,
        actor: str,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        detail: str = "",
    ) -> WorkspaceSourceRef:
        with self.database.connection() as connection:
            current = connection.execute("SELECT * FROM workspace_source_refs WHERE id = ?", (ref_id,)).fetchone()
            if current is None:
                raise WorkspaceSourceRefNotFoundError("Workspace candidate was not found")
            if current["status"] != "candidate":
                raise RuntimeError("Workspace candidate is no longer pending")
            now = utc_now()
            cursor = connection.execute(
                """UPDATE workspace_source_refs
                SET status = ?, target_type = ?, target_id = ?, updated_at = ?
                WHERE id = ? AND status = 'candidate'""",
                (status, target_type, target_id, now, ref_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Workspace candidate is no longer pending")
            self._audit(connection, ref_id, status, actor, detail)
            row = connection.execute("SELECT * FROM workspace_source_refs WHERE id = ?", (ref_id,)).fetchone()
        return _ref(row)

    @staticmethod
    def _audit(connection: sqlite3.Connection, ref_id: int, event: str, actor: str, detail: str = "") -> None:
        connection.execute(
            """INSERT INTO workspace_bridge_audit_log(ref_id, event, actor, detail, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (ref_id, event, actor, detail, utc_now()),
        )


def _ref(row: sqlite3.Row) -> WorkspaceSourceRef:
    return WorkspaceSourceRef(**dict(row))
