"""Parameterized SQLite repositories for Workspace Memory."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.memory.models import Decision, Note, Project, Task, WorkSession

PROJECT_STATUSES = frozenset({"active", "paused", "completed", "archived"})
TASK_STATUSES = frozenset({"todo", "doing", "done", "cancelled"})
TASK_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})


class MemoryError(RuntimeError):
    """Base error for safe Workspace Memory domain failures."""


class DuplicateProjectError(MemoryError):
    """Raised when a project name already exists."""


class ProjectNotFoundError(MemoryError):
    """Raised when a requested project does not exist."""


class InvalidMemoryValueError(MemoryError):
    """Raised when a domain status, priority, or required value is invalid."""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project(row: sqlite3.Row) -> Project:
    return Project(**dict(row))


def _task(row: sqlite3.Row) -> Task:
    return Task(**dict(row))


def _note(row: sqlite3.Row) -> Note:
    return Note(**dict(row))


def _decision(row: sqlite3.Row) -> Decision:
    return Decision(**dict(row))


def _session(row: sqlite3.Row) -> WorkSession:
    return WorkSession(**dict(row))


class MemoryRepository:
    """Persist and retrieve Workspace Memory records through parameterized SQL."""

    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def create_project(self, name: str, description: str) -> Project:
        now = utc_now()
        try:
            with self.database.connection() as connection:
                cursor = connection.execute(
                    "INSERT INTO projects (name, description, status, created_at, updated_at) "
                    "VALUES (?, ?, 'active', ?, ?)",
                    (name, description, now, now),
                )
                row = connection.execute("SELECT * FROM projects WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except MemoryDatabaseError:
            raise
        except sqlite3.IntegrityError as error:
            raise DuplicateProjectError("Project already exists") from error
        return _project(row)

    def list_projects(self) -> list[Project]:
        return self._many("SELECT * FROM projects ORDER BY created_at DESC, id DESC", (), _project)

    def get_project(self, identifier: str | int) -> Project:
        if isinstance(identifier, int):
            query, params = "SELECT * FROM projects WHERE id = ?", (identifier,)
        else:
            query, params = "SELECT * FROM projects WHERE name = ? COLLATE NOCASE", (identifier,)
        with self.database.connection() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            raise ProjectNotFoundError("Project not found")
        return _project(row)

    def update_project_status(self, project_id: int, status: str) -> Project:
        if status not in PROJECT_STATUSES:
            raise InvalidMemoryValueError("Invalid project status")
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?", (status, now, project_id)
            )
            if cursor.rowcount == 0:
                raise ProjectNotFoundError("Project not found")
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _project(row)

    def get_active_project(self) -> Project | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE status = 'active' ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return _project(row) if row else None

    def create_task(self, project_id: int, title: str, priority: str, description: str = "") -> Task:
        if priority not in TASK_PRIORITIES:
            raise InvalidMemoryValueError("Invalid task priority")
        now = utc_now()
        try:
            with self.database.connection() as connection:
                cursor = connection.execute(
                    "INSERT INTO tasks (project_id, title, description, status, priority, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'todo', ?, ?, ?)",
                    (project_id, title, description, priority, now, now),
                )
                row = connection.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except MemoryDatabaseError:
            raise
        except sqlite3.IntegrityError as error:
            raise ProjectNotFoundError("Project not found") from error
        return _task(row)

    def list_tasks(self, project_id: int, status: str | None = None) -> list[Task]:
        if status is not None and status not in TASK_STATUSES:
            raise InvalidMemoryValueError("Invalid task status")
        if status is None:
            return self._many("SELECT * FROM tasks WHERE project_id = ? ORDER BY id DESC", (project_id,), _task)
        return self._many(
            "SELECT * FROM tasks WHERE project_id = ? AND status = ? ORDER BY id DESC",
            (project_id, status),
            _task,
        )

    def update_task_status(self, task_id: int, status: str) -> Task:
        if status not in TASK_STATUSES:
            raise InvalidMemoryValueError("Invalid task status")
        now = utc_now()
        completed_at = now if status == "done" else None
        with self.database.connection() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = ?, updated_at = ?, completed_at = ? WHERE id = ?",
                (status, now, completed_at, task_id),
            )
            if cursor.rowcount == 0:
                raise InvalidMemoryValueError("Task not found")
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task(row)

    def task_counts(self, project_id: int) -> dict[str, int]:
        counts = {status: 0 for status in TASK_STATUSES}
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM tasks WHERE project_id = ? GROUP BY status", (project_id,)
            ).fetchall()
        for row in rows:
            counts[row["status"]] = row["count"]
        return counts

    def create_note(self, project_id: int, content: str) -> Note:
        now = utc_now()
        try:
            with self.database.connection() as connection:
                cursor = connection.execute(
                    "INSERT INTO notes (project_id, content, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (project_id, content, now, now),
                )
                row = connection.execute("SELECT * FROM notes WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except MemoryDatabaseError:
            raise
        except sqlite3.IntegrityError as error:
            raise ProjectNotFoundError("Project not found") from error
        return _note(row)

    def list_notes(self, project_id: int, limit: int = 3) -> list[Note]:
        return self._many(
            "SELECT * FROM notes WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT ?", (project_id, limit), _note
        )

    def create_decision(self, project_id: int, decision: str, reason: str | None) -> Decision:
        now = utc_now()
        try:
            with self.database.connection() as connection:
                cursor = connection.execute(
                    "INSERT INTO decisions (project_id, decision, reason, created_at) VALUES (?, ?, ?, ?)",
                    (project_id, decision, reason, now),
                )
                row = connection.execute("SELECT * FROM decisions WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except MemoryDatabaseError:
            raise
        except sqlite3.IntegrityError as error:
            raise ProjectNotFoundError("Project not found") from error
        return _decision(row)

    def list_decisions(self, project_id: int, limit: int = 3) -> list[Decision]:
        return self._many(
            "SELECT * FROM decisions WHERE project_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (project_id, limit),
            _decision,
        )

    def create_session(
        self, project_id: int, summary: str, completed_items: str, next_action: str, started_at: str, ended_at: str | None
    ) -> WorkSession:
        now = utc_now()
        try:
            with self.database.connection() as connection:
                cursor = connection.execute(
                    "INSERT INTO sessions "
                    "(project_id, summary, completed_items, next_action, started_at, ended_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (project_id, summary, completed_items, next_action, started_at, ended_at, now),
                )
                row = connection.execute("SELECT * FROM sessions WHERE id = ?", (cursor.lastrowid,)).fetchone()
        except MemoryDatabaseError:
            raise
        except sqlite3.IntegrityError as error:
            raise ProjectNotFoundError("Project not found") from error
        return _session(row)

    def latest_session(self, project_id: int) -> WorkSession | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE project_id = ? ORDER BY started_at DESC, id DESC LIMIT 1", (project_id,)
            ).fetchone()
        return _session(row) if row else None

    def _many(self, query: str, params: tuple[object, ...], factory):
        with self.database.connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [factory(row) for row in rows]
