import pytest

from app.memory.database import MemoryDatabase
from app.memory.repositories import (
    AmbiguousTaskError,
    InvalidMemoryValueError,
    InvalidTaskStatusError,
    ProjectNotFoundError,
    TaskNotFoundError,
    TaskStatusUnchangedError,
)
from app.memory.services import WorkspaceMemoryService


@pytest.fixture
def memory(tmp_path) -> WorkspaceMemoryService:
    service = WorkspaceMemoryService(MemoryDatabase(tmp_path / "workspace.sqlite3"))
    service.initialize()
    service.create_project("NOVA")
    return service


def test_task_status_lifecycle_and_completed_timestamp(memory) -> None:
    task = memory.create_task("NOVA", "Build session commands", "high")

    doing = memory.update_task_status_for_project("NOVA", str(task.id), "doing")
    done = memory.update_task_status_for_project("NOVA", "Build session commands", "done")
    reopened = memory.update_task_status_for_project("NOVA", str(task.id), "todo")

    assert doing.previous_status == "todo" and doing.task.status == "doing"
    assert done.task.completed_at is not None and done.task.completed_at.endswith("Z")
    assert reopened.previous_status == "done" and reopened.task.completed_at is None
    with pytest.raises(TaskStatusUnchangedError):
        memory.update_task_status_for_project("NOVA", str(task.id), "todo")
    with pytest.raises(InvalidTaskStatusError):
        memory.update_task_status_for_project("NOVA", str(task.id), "blocked")


def test_task_lookup_by_title_id_and_ambiguous_title(memory) -> None:
    first = memory.create_task("NOVA", "Duplicate title")
    second = memory.create_task("NOVA", "Duplicate title")
    project = memory.get_project("NOVA")

    assert memory.repository.get_task_by_id(project.id, first.id).id == first.id
    assert [task.id for task in memory.repository.find_tasks_by_exact_title(project.id, "duplicate title")] == [
        first.id,
        second.id,
    ]
    with pytest.raises(AmbiguousTaskError) as error:
        memory.update_task_status_for_project("NOVA", "Duplicate title", "doing")
    assert [task.id for task in error.value.tasks] == [first.id, second.id]
    with pytest.raises(TaskNotFoundError):
        memory.update_task_status_for_project("NOVA", "999", "doing")


def test_sessions_support_optional_fields_recent_order_and_limit(memory) -> None:
    summary_only = memory.create_session("NOVA", "First session", "", "")
    full = memory.create_session("NOVA", "Second session", "Tests passed", "Prepare review")

    project, sessions = memory.list_recent_sessions("NOVA")

    assert project.name == "NOVA"
    assert [session.id for session in sessions] == [full.id, summary_only.id]
    assert full.ended_at is not None and full.ended_at.endswith("Z")
    assert memory.repository.latest_session(project.id).id == full.id
    with pytest.raises(InvalidMemoryValueError):
        memory.list_recent_sessions("NOVA", 0)
    with pytest.raises(InvalidMemoryValueError):
        memory.list_recent_sessions("NOVA", 11)
    with pytest.raises(ProjectNotFoundError):
        memory.create_session("Missing", "Nope", "", "")


def test_continue_prefers_latest_session_next_action(memory) -> None:
    memory.create_task("NOVA", "Todo task", "urgent")
    doing = memory.create_task("NOVA", "Doing task", "normal")
    memory.update_task_status(doing.id, "doing")
    memory.create_session("NOVA", "Review complete", "Tests passed", "Ship review")

    context = memory.continue_context("NOVA")

    assert context.recommended_next_action == "Ship review"
