"""Persistence boundary for Executive Control Tower metadata."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from app.control_tower.models import Approval, Decision, WorkItem
from app.memory.database import MemoryDatabase


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControlTowerError(RuntimeError):
    """Safe public domain failure."""

class WorkItemNotFoundError(ControlTowerError): pass
class DecisionNotFoundError(ControlTowerError): pass
class ApprovalNotFoundError(ControlTowerError): pass
class InvalidStateTransitionError(ControlTowerError): pass
class InvalidCategoryError(ControlTowerError): pass
class ValidationError(ControlTowerError): pass
class ConflictError(ControlTowerError): pass


def _work_item(row: sqlite3.Row, dependencies: list[str]) -> WorkItem:
    return WorkItem(row["item_id"], row["project_id"], row["category"], row["title"],
                    row["summary"], row["priority_score"], row["urgency"], row["importance"],
                    row["deadline"], dependencies, row["clarification_needs"],
                    row["recommended_route"], row["status"], row["created_at"], row["updated_at"])


def _decision(row: sqlite3.Row) -> Decision:
    return Decision(**dict(row))


class ControlTowerRepository:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def project_exists(self, project_id: int) -> bool:
        with self.database.connection() as connection:
            return connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is not None

    def create_work_item(self, item: WorkItem, *, actor: str, correlation_id: str | None = None) -> None:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO control_tower_work_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (item.item_id, item.project_id, item.category, item.title, item.summary,
                     item.priority_score, item.urgency, item.importance, item.deadline,
                     item.clarification_needs, item.recommended_route, item.status,
                     item.created_at, item.updated_at),
                )
                connection.executemany(
                    "INSERT INTO control_tower_work_dependencies(item_id, depends_on_item_id) VALUES (?, ?)",
                    [(item.item_id, dependency) for dependency in item.dependencies],
                )
                self._audit(connection, "capture", "work_item", item.item_id, actor=actor,
                            to_state=item.status, correlation_id=correlation_id,
                            metadata={"category": item.category, "route": item.recommended_route})
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                connection.execute("ROLLBACK")
                raise ValidationError("Work item could not be saved") from error
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_work_item(self, item_id: str) -> WorkItem | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM control_tower_work_items WHERE item_id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            dependencies = [r[0] for r in connection.execute(
                "SELECT depends_on_item_id FROM control_tower_work_dependencies WHERE item_id = ? ORDER BY depends_on_item_id", (item_id,)
            )]
            return _work_item(row, dependencies)

    def list_work_items(self, statuses: Iterable[str] | None = None) -> list[WorkItem]:
        with self.database.connection() as connection:
            values = tuple(statuses or ())
            sql = "SELECT item_id FROM control_tower_work_items"
            if values:
                sql += " WHERE status IN (" + ",".join("?" for _ in values) + ")"
            sql += " ORDER BY updated_at DESC, item_id DESC"
            identifiers = [row[0] for row in connection.execute(sql, values)]
        return [item for item_id in identifiers if (item := self.get_work_item(item_id)) is not None]

    def transition_work_item(self, item_id: str, *, expected_status: str, new_status: str, actor: str, correlation_id: str | None = None) -> None:
        now = utc_now()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "UPDATE control_tower_work_items SET status = ?, updated_at = ? WHERE item_id = ? AND status = ?",
                    (new_status, now, item_id, expected_status),
                )
                if cursor.rowcount != 1:
                    if connection.execute("SELECT 1 FROM control_tower_work_items WHERE item_id = ?", (item_id,)).fetchone() is None:
                        raise WorkItemNotFoundError("Work item was not found")
                    raise ConflictError("Work item was updated elsewhere")
                self._audit(connection, "transition", "work_item", item_id, actor=actor,
                            from_state=expected_status, to_state=new_status, correlation_id=correlation_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def dependency_statuses(self, item_id: str) -> list[str]:
        with self.database.connection() as connection:
            return [row[0] for row in connection.execute(
                "SELECT w.status FROM control_tower_work_dependencies d "
                "JOIN control_tower_work_items w ON w.item_id = d.depends_on_item_id "
                "WHERE d.item_id = ? ORDER BY d.depends_on_item_id", (item_id,)
            )]

    def create_decision(self, decision: Decision, *, actor: str, correlation_id: str | None = None) -> None:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO control_tower_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (decision.decision_id, decision.project_id, decision.summary, decision.rationale,
                     decision.impact, decision.approved_by, decision.effective_date, decision.status,
                     decision.supersedes, decision.superseded_by, decision.created_at),
                )
                if decision.supersedes:
                    cursor = connection.execute(
                        "UPDATE control_tower_decisions SET superseded_by = ?, status = 'superseded' "
                        "WHERE decision_id = ? AND superseded_by IS NULL", (decision.decision_id, decision.supersedes),
                    )
                    if cursor.rowcount != 1:
                        raise ConflictError("Decision cannot be superseded")
                self._audit(connection, "decision_create", "decision", decision.decision_id, actor=actor,
                            to_state=decision.status, correlation_id=correlation_id,
                            metadata={"supersedes": decision.supersedes})
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                connection.execute("ROLLBACK")
                raise ValidationError("Decision could not be saved") from error
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_decision(self, decision_id: str) -> Decision | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM control_tower_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        return None if row is None else _decision(row)

    def create_approval_link(self, approval: Approval, *, actor: str, correlation_id: str | None = None) -> None:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO control_tower_approval_links VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (approval.approval_id, approval.source_system, approval.source_item_id,
                     approval.requested_action, approval.status, approval.requested_at,
                     approval.resolved_at, approval.resolved_by),
                )
                self._audit(connection, "approval_create", "approval", approval.approval_id, actor=actor,
                            to_state=approval.status, correlation_id=correlation_id)
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                connection.execute("ROLLBACK")
                raise ValidationError("Approval request could not be saved") from error
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def resolve_approval_link(self, approval_id: str, *, status: str, actor: str) -> None:
        if status not in {"approved", "rejected"}:
            raise ValidationError("Approval state is invalid")
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute("SELECT status FROM control_tower_approval_links WHERE approval_id = ?", (approval_id,)).fetchone()
                if row is None:
                    raise ApprovalNotFoundError("Approval was not found")
                if row["status"] != "pending":
                    raise ConflictError("Approval has already been resolved")
                connection.execute("UPDATE control_tower_approval_links SET status = ?, resolved_at = ?, resolved_by = ? WHERE approval_id = ?", (status, utc_now(), actor, approval_id))
                self._audit(connection, "approval_state_change", "approval", approval_id, actor=actor, from_state="pending", to_state=status)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_approval(self, approval_id: str) -> Approval | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM control_tower_approval_links WHERE approval_id = ?", (approval_id,)).fetchone()
        return None if row is None else Approval(**dict(row))

    def list_pending_link_approvals(self) -> list[Approval]:
        with self.database.connection() as connection:
            rows = connection.execute("SELECT * FROM control_tower_approval_links WHERE status = 'pending' ORDER BY requested_at, approval_id").fetchall()
        return [Approval(**dict(row)) for row in rows]

    def audit_aggregation(self, operation: str, actor: str = "system") -> None:
        with self.database.connection() as connection:
            self._audit(connection, operation, "aggregation", operation, actor=actor)

    @staticmethod
    def _audit(connection: sqlite3.Connection, operation: str, entity_type: str, entity_id: str, *, actor: str,
               from_state: str | None = None, to_state: str | None = None, correlation_id: str | None = None,
               metadata: dict[str, object] | None = None) -> None:
        connection.execute(
            "INSERT INTO control_tower_audit_log(operation, entity_type, entity_id, from_state, to_state, actor, correlation_id, outcome, metadata, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, 'success', ?, ?)",
            (operation, entity_type, entity_id, from_state, to_state, actor, correlation_id,
             json.dumps(metadata or {}, sort_keys=True), utc_now()),
        )
