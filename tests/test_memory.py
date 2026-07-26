import sqlite3

import pytest

from app.memory.database import MemoryDatabase
from app.memory.repositories import (
    DuplicateProjectError,
    InvalidMemoryValueError,
    ProjectNotFoundError,
)
from app.memory.services import WorkspaceMemoryService


@pytest.fixture
def memory(tmp_path) -> WorkspaceMemoryService:
    service = WorkspaceMemoryService(MemoryDatabase(tmp_path / "nested" / "workspace.sqlite3"))
    service.initialize()
    return service


def test_database_initialization_creates_schema_and_enforces_foreign_keys(memory) -> None:
    assert memory.database.path.exists()
    with memory.database.connection() as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert {"projects", "tasks", "notes", "decisions", "sessions"} <= tables
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO tasks (project_id, title, status, priority, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (999, "Invalid", "todo", "normal", "2026-07-26T00:00:00Z", "2026-07-26T00:00:00Z"),
            )


def test_projects_are_case_insensitive_unique_and_listed(memory) -> None:
    created = memory.create_project("NOVA AI Office", "Workspace Memory")

    assert created.status == "active"
    assert memory.get_project("nova ai office").id == created.id
    assert [project.name for project in memory.list_projects()] == ["NOVA AI Office"]
    with pytest.raises(DuplicateProjectError):
        memory.create_project("nova ai office")


def test_project_status_validation_and_active_project(memory) -> None:
    memory.create_project("Older Project")
    newest = memory.create_project("Newest Project")

    assert memory.active_project().id == newest.id
    assert memory.update_project_status("Newest Project", "paused").status == "paused"
    assert memory.active_project().name == "Older Project"
    with pytest.raises(InvalidMemoryValueError):
        memory.update_project_status("Older Project", "invalid")


def test_tasks_list_filter_progress_and_completion_timestamp(memory) -> None:
    memory.create_project("NOVA")
    todo = memory.create_task("NOVA", "Write tests")
    doing = memory.create_task("NOVA", "Build database", "high")
    done = memory.create_task("NOVA", "Plan schema")
    cancelled = memory.create_task("NOVA", "Discard idea")
    memory.update_task_status(doing.id, "doing")
    completed = memory.update_task_status(done.id, "done")
    memory.update_task_status(cancelled.id, "cancelled")

    _, doing_tasks = memory.list_tasks("NOVA", "doing")
    _, progress, *_ = memory.progress("NOVA")

    assert [task.id for task in doing_tasks] == [doing.id]
    assert completed.completed_at is not None and completed.completed_at.endswith("Z")
    assert progress.total == 4
    assert progress.todo == 1
    assert progress.done == 1
    assert progress.cancelled == 1
    assert progress.completion_percentage == 33
    with pytest.raises(ProjectNotFoundError):
        memory.repository.create_task(999, "Invalid", "normal")
    with pytest.raises(InvalidMemoryValueError):
        memory.create_task("NOVA", "Invalid", "critical")


def test_notes_decisions_sessions_resume_and_continue(memory) -> None:
    memory.create_project("NOVA")
    memory.create_task("NOVA", "Build database", "high")
    memory.create_note("NOVA", "SQLite selected.")
    decision = memory.create_decision("NOVA", "Use SQLite", None)
    session = memory.create_session(
        "NOVA",
        "Schema completed",
        "Created tables",
        "Add handlers",
    )

    _, notes = memory.list_notes("NOVA")
    _, decisions = memory.list_decisions("NOVA")
    resume = memory.resume("NOVA")
    continuation = memory.continue_context("NOVA")

    assert notes[0].content == "SQLite selected."
    assert decisions[0].id == decision.id and decisions[0].reason is None
    assert resume.latest_session.id == session.id
    assert resume.notes[0].content == "SQLite selected."
    assert continuation.latest_session.next_action == "Add handlers"
    assert continuation.unfinished_tasks[0].title == "Build database"
    with pytest.raises(ProjectNotFoundError):
        memory.create_note("Missing", "Nope")
    with pytest.raises(ProjectNotFoundError):
        memory.create_decision("Missing", "Nope")
    with pytest.raises(ProjectNotFoundError):
        memory.repository.create_session(999, "Nope", "", "", "2026-07-26T00:00:00Z", None)


def test_sensitive_values_are_rejected_before_storage(memory) -> None:
    memory.create_project("NOVA")

    with pytest.raises(InvalidMemoryValueError, match="Sensitive values"):
        memory.create_note("NOVA", "TELEGRAM_BOT_TOKEN=not-a-real-token")
