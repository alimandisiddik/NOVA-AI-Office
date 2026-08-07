"""SQLite persistence for dispatch and approval records.

All state-changing helpers receive one connection from their caller so the
associated state and audit rows commit or roll back together.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.dispatch.errors import ApprovalNotFoundError, DispatchNotFoundError
from app.dispatch.models import ApprovalRequest, DispatchRecord
from app.memory.database import MemoryDatabase


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DispatchRepository:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    @staticmethod
    def _dispatch(row: sqlite3.Row) -> DispatchRecord:
        return DispatchRecord(
            dispatch_id=row["dispatch_id"], source_type=row["source_type"],
            source_id=row["source_id"], agent_id=row["agent_id"],
            capability=row["capability"], payload_ref=row["payload_ref"],
            status=row["status"], attempt_count=row["attempt_count"],
            max_attempts=row["max_attempts"], idempotency_key=row["idempotency_key"],
            correlation_id=row["correlation_id"], requested_by=row["requested_by"],
            result_summary=row["result_summary"], created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _approval(row: sqlite3.Row) -> ApprovalRequest:
        return ApprovalRequest(
            approval_id=row["approval_id"], dispatch_id=row["dispatch_id"],
            requested_action=row["requested_action"], status=row["status"],
            requested_by=row["requested_by"], requested_at=row["requested_at"],
            resolved_by=row["resolved_by"], resolved_at=row["resolved_at"],
            expires_at=row["expires_at"],
        )

    def get_dispatch(self, dispatch_id: str) -> DispatchRecord:
        with self.database.connection() as connection:
            return self.get_dispatch_in(connection, dispatch_id)

    def get_dispatch_in(self, connection: sqlite3.Connection, dispatch_id: str) -> DispatchRecord:
        row = connection.execute("SELECT * FROM dispatches WHERE dispatch_id = ?", (dispatch_id,)).fetchone()
        if row is None:
            raise DispatchNotFoundError("Dispatch was not found.")
        return self._dispatch(row)

    def find_idempotent(self, request: object) -> DispatchRecord | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM dispatches WHERE source_type = ? AND source_id = ? AND idempotency_key = ?",
                (request.source_type, request.source_id, request.idempotency_key),
            ).fetchone()
            return self._dispatch(row) if row is not None else None

    def insert_dispatch_in(self, connection: sqlite3.Connection, dispatch: DispatchRecord, actor: str) -> None:
        connection.execute(
            """INSERT INTO dispatches (
                dispatch_id, source_type, source_id, agent_id, capability, payload_ref,
                idempotency_key, correlation_id, status, attempt_count, max_attempts,
                requested_by, result_summary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dispatch.dispatch_id, dispatch.source_type, dispatch.source_id, dispatch.agent_id,
             dispatch.capability, dispatch.payload_ref, dispatch.idempotency_key,
             dispatch.correlation_id, dispatch.status, dispatch.attempt_count,
             dispatch.max_attempts, dispatch.requested_by, dispatch.result_summary,
             dispatch.created_at, dispatch.updated_at),
        )
        self.audit_dispatch_in(connection, dispatch.dispatch_id, "created", actor, None, dispatch.status, dispatch.correlation_id)

    def audit_dispatch_in(self, connection: sqlite3.Connection, dispatch_id: str, event: str, actor: str,
                          from_status: str | None, to_status: str | None,
                          correlation_id: str | None = None, detail: str = "") -> None:
        connection.execute(
            """INSERT INTO dispatch_audit_log
            (dispatch_id, event, actor, from_status, to_status, correlation_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (dispatch_id, event, actor, from_status, to_status, correlation_id, detail, utc_now()),
        )

    def transition_dispatch_in(self, connection: sqlite3.Connection, dispatch_id: str, expected: str,
                               target: str, actor: str, *, event: str = "status_changed",
                               correlation_id: str | None = None, detail: str = "",
                               result_summary: str | None = None) -> bool:
        now = utc_now()
        sql = "UPDATE dispatches SET status = ?, updated_at = ?"
        values: list[object] = [target, now]
        if result_summary is not None:
            sql += ", result_summary = ?"
            values.append(result_summary)
        sql += " WHERE dispatch_id = ? AND status = ?"
        values.extend([dispatch_id, expected])
        updated = connection.execute(sql, values).rowcount == 1
        if updated:
            self.audit_dispatch_in(connection, dispatch_id, event, actor, expected, target, correlation_id, detail)
        return updated

    def increment_attempt_in(self, connection: sqlite3.Connection, dispatch_id: str, expected_status: str,
                             actor: str) -> int:
        record = self.get_dispatch_in(connection, dispatch_id)
        if record.status != expected_status:
            return -1
        attempt_number = record.attempt_count + 1
        now = utc_now()
        updated = connection.execute(
            "UPDATE dispatches SET attempt_count = ?, updated_at = ? WHERE dispatch_id = ? AND status = ?",
            (attempt_number, now, dispatch_id, expected_status),
        ).rowcount
        if updated != 1:
            return -1
        connection.execute(
            "INSERT INTO dispatch_attempts (dispatch_id, attempt_number, status, started_at) VALUES (?, ?, 'running', ?)",
            (dispatch_id, attempt_number, now),
        )
        self.audit_dispatch_in(connection, dispatch_id, "attempt_started", actor, expected_status, expected_status)
        return attempt_number

    def finish_attempt_in(self, connection: sqlite3.Connection, dispatch_id: str, attempt_number: int,
                          status: str, summary: str) -> None:
        connection.execute(
            """UPDATE dispatch_attempts SET status = ?, ended_at = ?, result_summary = ?
            WHERE dispatch_id = ? AND attempt_number = ?""",
            (status, utc_now(), summary, dispatch_id, attempt_number),
        )

    def update_dispatch_status(self, dispatch_id: str, new_status: str, actor: str, expected_status: str | None = None, correlation_id: str | None = None) -> bool:
        """Compatibility helper for legacy tests and controlled sync callers."""
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self.get_dispatch_in(connection, dispatch_id)
            expected = record.status if expected_status is None else expected_status
            return self.transition_dispatch_in(connection, dispatch_id, expected, new_status, actor, correlation_id=correlation_id)

    def increment_attempt(self, dispatch_id: str, actor: str) -> None:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self.get_dispatch_in(connection, dispatch_id)
            next_number = record.attempt_count + 1
            connection.execute("UPDATE dispatches SET attempt_count = ?, updated_at = ? WHERE dispatch_id = ?", (next_number, utc_now(), dispatch_id))
            self.audit_dispatch_in(connection, dispatch_id, "attempt_incremented", actor, record.status, record.status)

    def get_approval(self, approval_id: str) -> ApprovalRequest:
        with self.database.connection() as connection:
            return self.get_approval_in(connection, approval_id)

    def get_approval_in(self, connection: sqlite3.Connection, approval_id: str) -> ApprovalRequest:
        row = connection.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        if row is None:
            raise ApprovalNotFoundError("Approval was not found.")
        return self._approval(row)

    def get_open_approval_in(self, connection: sqlite3.Connection, dispatch_id: str) -> ApprovalRequest | None:
        row = connection.execute(
            "SELECT * FROM approvals WHERE dispatch_id = ? AND status = 'requested' ORDER BY requested_at LIMIT 1",
            (dispatch_id,),
        ).fetchone()
        return self._approval(row) if row is not None else None

    def insert_approval_in(self, connection: sqlite3.Connection, approval: ApprovalRequest, actor: str,
                           detail: str = "") -> None:
        connection.execute(
            """INSERT INTO approvals
            (approval_id, dispatch_id, requested_action, status, requested_by, requested_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (approval.approval_id, approval.dispatch_id, approval.requested_action, approval.status,
             approval.requested_by, approval.requested_at, approval.expires_at),
        )
        self.audit_approval_in(connection, approval.approval_id, "requested", actor, detail)

    def audit_approval_in(self, connection: sqlite3.Connection, approval_id: str, event: str, actor: str,
                          detail: str = "") -> None:
        connection.execute(
            "INSERT INTO approval_audit (approval_id, event, actor, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (approval_id, event, actor, detail, utc_now()),
        )

    def transition_approval_in(self, connection: sqlite3.Connection, approval_id: str, expected: str,
                               target: str, actor: str, detail: str = "") -> bool:
        now = utc_now()
        updated = connection.execute(
            """UPDATE approvals SET status = ?, resolved_by = ?, resolved_at = ?
            WHERE approval_id = ? AND status = ?""",
            (target, actor, now, approval_id, expected),
        ).rowcount == 1
        if updated:
            self.audit_approval_in(connection, approval_id, target, actor, detail)
        return updated

    def list_dispatches(self, *, status: str | None, source_type: str | None, agent_id: str | None,
                        limit: int) -> list[DispatchRecord]:
        clauses, params = ["1 = 1"], []
        for value, column in ((status, "status"), (source_type, "source_type"), (agent_id, "agent_id")):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        params.append(limit)
        with self.database.connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM dispatches WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
            return [self._dispatch(row) for row in rows]

    def list_pending_approvals(self, *, source_type: str | None = None) -> list[ApprovalRequest]:
        sql = "SELECT a.* FROM approvals a"
        params: list[object] = []
        if source_type is not None:
            sql += " JOIN dispatches d ON d.dispatch_id = a.dispatch_id WHERE a.status = 'requested' AND d.source_type = ?"
            params.append(source_type)
        else:
            sql += " WHERE a.status = 'requested'"
        sql += " ORDER BY a.requested_at, a.approval_id"
        with self.database.connection() as connection:
            return [self._approval(row) for row in connection.execute(sql, params).fetchall()]
