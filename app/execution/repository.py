"""Parameterized SQLite repository for NOVA execution orchestration.

All SQL lives here.  No business logic, no state-machine decisions.
Only safe, parameterized queries are used.  No user-supplied string
is ever interpolated directly into SQL.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.execution.models import AuditEntry, ExecutionRecord, ExecutionState
from app.execution.schema import EXECUTION_SCHEMA


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _row_to_record(row: sqlite3.Row) -> ExecutionRecord:
    return ExecutionRecord(
        id=row["id"],
        instruction_hash=row["instruction_hash"],
        risk_level=row["risk_level"],
        workflow_id=row["workflow_id"],
        state=row["state"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        result_summary=row["result_summary"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_audit(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        id=row["id"],
        execution_id=row["execution_id"],
        event=row["event"],
        actor=row["actor"],
        detail=row["detail"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------


class ExecutionError(RuntimeError):
    """Base error for execution domain failures."""


class ExecutionNotFoundError(ExecutionError):
    """Raised when a requested execution ID does not exist."""


class InvalidTransitionError(ExecutionError):
    """Raised when a state transition is not permitted."""


class ApprovalError(ExecutionError):
    """Raised when an approval attempt violates policy."""


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class ExecutionRepository:
    """Parameterized SQLite queries for the executions subsystem."""

    def __init__(self, database: MemoryDatabase) -> None:
        self._db = database

    # ------------------------------------------------------------------ init

    def initialize(self) -> None:
        """Apply the execution schema additively (idempotent)."""
        try:
            with self._db.connection() as conn:
                conn.executescript(EXECUTION_SCHEMA)
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Execution schema initialization failed") from exc

    # --------------------------------------------------------------- create

    def create_execution(
        self,
        instruction_hash: str,
        risk_level: str,
        workflow_id: str,
        initial_state: str,
    ) -> ExecutionRecord:
        now = _utc_now()
        try:
            with self._db.connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO executions
                        (instruction_hash, risk_level, workflow_id, state,
                         result_summary, created_at, updated_at)
                    VALUES (?, ?, ?, ?, '', ?, ?)
                    """,
                    (instruction_hash, risk_level, workflow_id, initial_state, now, now),
                )
                row_id = cursor.lastrowid
                row = conn.execute(
                    "SELECT * FROM executions WHERE id = ?", (row_id,)
                ).fetchone()
                return _row_to_record(row)
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Failed to create execution record") from exc

    # ---------------------------------------------------------------- fetch

    def get_execution(self, execution_id: int) -> ExecutionRecord:
        try:
            with self._db.connection() as conn:
                row = conn.execute(
                    "SELECT * FROM executions WHERE id = ?", (execution_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Failed to fetch execution record") from exc
        if row is None:
            raise ExecutionNotFoundError(f"Execution #{execution_id} not found")
        return _row_to_record(row)

    # --------------------------------------------------------------- update

    def transition_state(
        self,
        execution_id: int,
        expected_state: str,
        new_state: str,
        result_summary: str = "",
    ) -> ExecutionRecord:
        """Atomically transition state only if the current state matches expected_state
        AND new_state is in the allowed set for expected_state per the transition matrix.
        """
        allowed = ExecutionState.TRANSITIONS.get(expected_state, frozenset())
        if new_state not in allowed:
            raise InvalidTransitionError(
                f"Transition '{expected_state}' → '{new_state}' is not permitted. "
                f"Allowed: {sorted(allowed) if allowed else 'none (terminal state)'}"
            )
        now = _utc_now()
        try:
            with self._db.connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE executions
                    SET state = ?, result_summary = ?, updated_at = ?
                    WHERE id = ? AND state = ?
                    """,
                    (new_state, result_summary, now, execution_id, expected_state),
                )
                if cursor.rowcount == 0:
                    # Either not found or state mismatch — fetch to distinguish
                    row = conn.execute(
                        "SELECT * FROM executions WHERE id = ?", (execution_id,)
                    ).fetchone()
                    if row is None:
                        raise ExecutionNotFoundError(
                            f"Execution #{execution_id} not found"
                        )
                    raise InvalidTransitionError(
                        f"Execution #{execution_id}: cannot transition from "
                        f"'{row['state']}' (expected '{expected_state}') to '{new_state}'"
                    )
                row = conn.execute(
                    "SELECT * FROM executions WHERE id = ?", (execution_id,)
                ).fetchone()
                return _row_to_record(row)
        except (ExecutionNotFoundError, InvalidTransitionError):
            raise
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Failed to transition execution state") from exc

    def record_approval(
        self,
        execution_id: int,
        approved_by: int,
    ) -> ExecutionRecord:
        """Set approved_by and approved_at, then transition to queued atomically."""
        now = _utc_now()
        try:
            with self._db.connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE executions
                    SET state = 'queued',
                        approved_by = ?,
                        approved_at = ?,
                        updated_at  = ?
                    WHERE id = ? AND state = 'awaiting_approval'
                    """,
                    (approved_by, now, now, execution_id),
                )
                if cursor.rowcount == 0:
                    row = conn.execute(
                        "SELECT * FROM executions WHERE id = ?", (execution_id,)
                    ).fetchone()
                    if row is None:
                        raise ExecutionNotFoundError(
                            f"Execution #{execution_id} not found"
                        )
                    raise ApprovalError(
                        f"Execution #{execution_id} is not in awaiting_approval "
                        f"(current state: '{row['state']}')"
                    )
                row = conn.execute(
                    "SELECT * FROM executions WHERE id = ?", (execution_id,)
                ).fetchone()
                return _row_to_record(row)
        except (ExecutionNotFoundError, ApprovalError):
            raise
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Failed to record approval") from exc

    # --------------------------------------------------------------- audit

    def add_audit_entry(
        self,
        execution_id: int,
        event: str,
        actor: str,
        detail: str = "",
    ) -> AuditEntry:
        now = _utc_now()
        try:
            with self._db.connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO execution_audit_log
                        (execution_id, event, actor, detail, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (execution_id, event, actor, detail, now),
                )
                row = conn.execute(
                    "SELECT * FROM execution_audit_log WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                return _row_to_audit(row)
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Failed to write audit entry") from exc

    def list_audit_entries(self, execution_id: int) -> list[AuditEntry]:
        try:
            with self._db.connection() as conn:
                rows = conn.execute(
                    "SELECT * FROM execution_audit_log WHERE execution_id = ? "
                    "ORDER BY id ASC",
                    (execution_id,),
                ).fetchall()
                return [_row_to_audit(r) for r in rows]
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Failed to list audit entries") from exc

    # --------------------------------------------------------- reconcile

    def reconcile_running_to_failed(self) -> int:
        """Mark all 'running' executions as 'failed' on restart.

        Returns the count of executions reconciled.
        """
        now = _utc_now()
        summary = "Reconciled to failed on service restart"
        try:
            with self._db.connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE executions
                    SET state = 'failed',
                        result_summary = ?,
                        updated_at = ?
                    WHERE state = 'running'
                    """,
                    (summary, now),
                )
                return cursor.rowcount
        except sqlite3.Error as exc:
            raise MemoryDatabaseError("Failed to reconcile running executions") from exc

    def reconcile_running_to_failed_with_ids(self) -> list[int]:
        """Mark all 'running' executions as 'failed' on restart.

        Returns the list of execution IDs that were reconciled so that the
        caller can write one audit entry per execution.  Idempotent: if no
        executions are running, returns an empty list without any writes.
        """
        now = _utc_now()
        summary = "Reconciled to failed on service restart"
        try:
            with self._db.connection() as conn:
                rows = conn.execute(
                    "SELECT id FROM executions WHERE state = 'running'"
                ).fetchall()
                ids = [row["id"] for row in rows]
                if ids:
                    conn.execute(
                        """
                        UPDATE executions
                        SET state = 'failed',
                            result_summary = ?,
                            updated_at = ?
                        WHERE state = 'running'
                        """,
                        (summary, now),
                    )
                return ids
        except sqlite3.Error as exc:
            raise MemoryDatabaseError(
                "Failed to reconcile running executions"
            ) from exc
