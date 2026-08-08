"""SQLite persistence for assignment rows and their state-change audit trail."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.agent_assignment.errors import AssignmentNotFoundError
from app.agent_assignment.models import AgentAssignment
from app.memory.database import MemoryDatabase


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentAssignmentRepository:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    @staticmethod
    def _assignment(row: sqlite3.Row) -> AgentAssignment:
        return AgentAssignment(**dict(row))

    def get_assignment(self, assignment_id: str) -> AgentAssignment:
        with self.database.connection() as connection:
            return self.get_assignment_in(connection, assignment_id)

    def get_assignment_in(self, connection: sqlite3.Connection, assignment_id: str) -> AgentAssignment:
        row = connection.execute(
            "SELECT * FROM agent_assignments WHERE assignment_id = ?", (assignment_id,)
        ).fetchone()
        if row is None:
            raise AssignmentNotFoundError("Assignment was not found.")
        return self._assignment(row)

    def insert_assignment_in(
        self, connection: sqlite3.Connection, assignment: AgentAssignment, actor: str, *, detail: str = ""
    ) -> None:
        connection.execute(
            """INSERT INTO agent_assignments(
                assignment_id, work_item_id, requested_capability, assigned_agent_id,
                status, dispatch_id, requested_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assignment.assignment_id,
                assignment.work_item_id,
                assignment.requested_capability,
                assignment.assigned_agent_id,
                assignment.status,
                assignment.dispatch_id,
                assignment.requested_by,
                assignment.created_at,
                assignment.updated_at,
            ),
        )
        self.audit_in(connection, assignment.assignment_id, "proposed", actor, None, "proposed", detail)

    def transition_in(
        self,
        connection: sqlite3.Connection,
        assignment_id: str,
        expected_status: str,
        target_status: str,
        actor: str,
        *,
        event: str,
        detail: str = "",
        dispatch_id: str | None = None,
    ) -> bool:
        now = utc_now()
        if dispatch_id is None:
            cursor = connection.execute(
                """UPDATE agent_assignments SET status = ?, updated_at = ?
                   WHERE assignment_id = ? AND status = ?""",
                (target_status, now, assignment_id, expected_status),
            )
        else:
            cursor = connection.execute(
                """UPDATE agent_assignments SET status = ?, dispatch_id = ?, updated_at = ?
                   WHERE assignment_id = ? AND status = ? AND dispatch_id IS NULL""",
                (target_status, dispatch_id, now, assignment_id, expected_status),
            )
        if cursor.rowcount != 1:
            return False
        self.audit_in(connection, assignment_id, event, actor, expected_status, target_status, detail)
        return True

    def audit_in(
        self,
        connection: sqlite3.Connection,
        assignment_id: str,
        event: str,
        actor: str,
        from_status: str | None,
        to_status: str | None,
        detail: str,
    ) -> None:
        connection.execute(
            """INSERT INTO agent_assignment_audit_log(
                assignment_id, event, actor, from_status, to_status, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (assignment_id, event, actor, from_status, to_status, detail, utc_now()),
        )

    def list_assignments(self, *, status: str | None = None, limit: int = 20) -> list[AgentAssignment]:
        query = "SELECT * FROM agent_assignments"
        params: list[object] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, assignment_id DESC LIMIT ?"
        params.append(limit)
        with self.database.connection() as connection:
            return [self._assignment(row) for row in connection.execute(query, params).fetchall()]

    def get_active_for_work_item(self, work_item_id: str) -> AgentAssignment | None:
        with self.database.connection() as connection:
            row = connection.execute(
                """SELECT * FROM agent_assignments
                   WHERE work_item_id = ? AND status IN ('proposed', 'accepted', 'in_progress')
                   ORDER BY created_at DESC, assignment_id DESC LIMIT 1""",
                (work_item_id,),
            ).fetchone()
        return self._assignment(row) if row is not None else None
