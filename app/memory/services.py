"""Workspace Memory use cases independent of Telegram transport."""

from __future__ import annotations

import re

from app.memory.database import MemoryDatabase
from app.memory.models import ContinueContext, ProgressSummary, ResumeContext, TaskStatusUpdate
from app.memory.repositories import (
    AmbiguousTaskError,
    InvalidMemoryValueError,
    MemoryRepository,
    TASK_STATUSES,
    utc_now,
)

SENSITIVE_CONTENT_PATTERN = re.compile(
    r"(?:telegram_bot_token|api[_ -]?key|password|credential|secret|"
    r"authorization\s*:|bearer\s+|begin [a-z ]*private key|"
    r"^\s*[A-Z][A-Z0-9_]{2,}\s*=)",
    re.IGNORECASE,
)


class WorkspaceMemoryService:
    """Provide validated project-memory operations for NOVA interfaces."""

    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database
        self.repository = MemoryRepository(database)

    def initialize(self) -> None:
        self.database.initialize()

    def create_project(self, name: str, description: str = ""):
        return self.repository.create_project(
            self._safe_required(name, "Project name"), self._safe_optional(description)
        )

    def list_projects(self):
        return self.repository.list_projects()

    def get_project(self, name_or_id: str | int):
        return self.repository.get_project(name_or_id)

    def active_project(self):
        return self.repository.get_active_project()

    def update_project_status(self, project_name: str, status: str):
        project = self.get_project(self._safe_required(project_name, "Project name"))
        return self.repository.update_project_status(project.id, status.strip().lower())

    def create_task(self, project_name: str, title: str, priority: str = "normal"):
        project = self.get_project(self._safe_required(project_name, "Project name"))
        return self.repository.create_task(project.id, self._safe_required(title, "Task title"), priority.strip().lower())

    def list_tasks(self, project_name: str, status: str | None = None):
        project = self.get_project(self._safe_required(project_name, "Project name"))
        return project, self.repository.list_tasks(project.id, status.strip().lower() if status else None)

    def update_task_status(self, task_id: int, status: str):
        task, _ = self.repository.update_task_status(task_id, status.strip().lower())
        return task

    def update_task_status_for_project(
        self, project_name: str, task_identifier: str, status: str
    ) -> TaskStatusUpdate:
        project = self.get_project(self._safe_required(project_name, "Project name"))
        task = self.repository.resolve_task(project.id, self._safe_required(task_identifier, "Task identifier"))
        updated_task, previous_status = self.repository.update_task_status(task.id, status.strip().lower())
        return TaskStatusUpdate(task=updated_task, previous_status=previous_status)

    def create_note(self, project_name: str, content: str):
        project = self.get_project(self._safe_required(project_name, "Project name"))
        return self.repository.create_note(project.id, self._safe_required(content, "Note content"))

    def list_notes(self, project_name: str):
        project = self.get_project(self._safe_required(project_name, "Project name"))
        return project, self.repository.list_notes(project.id)

    def create_decision(self, project_name: str, decision: str, reason: str | None = None):
        project = self.get_project(self._safe_required(project_name, "Project name"))
        cleaned_reason = self._safe_optional(reason) if reason else None
        return self.repository.create_decision(project.id, self._safe_required(decision, "Decision"), cleaned_reason)

    def list_decisions(self, project_name: str):
        project = self.get_project(self._safe_required(project_name, "Project name"))
        return project, self.repository.list_decisions(project.id)

    def create_session(
        self,
        project_name: str,
        summary: str,
        completed_items: str,
        next_action: str,
        started_at: str | None = None,
        ended_at: str | None = None,
    ):
        project = self.get_project(self._safe_required(project_name, "Project name"))
        timestamp = utc_now()
        return self.repository.create_session(
            project.id,
            self._safe_required(summary, "Session summary"),
            self._safe_optional(completed_items),
            self._safe_optional(next_action),
            started_at or timestamp,
            ended_at or timestamp,
        )

    def list_recent_sessions(self, project_name: str, limit: int = 5):
        if not 1 <= limit <= 10:
            raise InvalidMemoryValueError("Session limit must be between 1 and 10")
        project = self.get_project(self._safe_required(project_name, "Project name"))
        return project, self.repository.list_recent_sessions(project.id, limit)

    def progress(self, project_name: str):
        project = self.get_project(self._safe_required(project_name, "Project name"))
        counts = self.repository.task_counts(project.id)
        non_cancelled = sum(counts[status] for status in TASK_STATUSES if status != "cancelled")
        percentage = round((counts["done"] / non_cancelled) * 100) if non_cancelled else 0
        return (
            project,
            ProgressSummary(
                total=sum(counts.values()),
                todo=counts["todo"],
                doing=counts["doing"],
                done=counts["done"],
                cancelled=counts["cancelled"],
                completion_percentage=percentage,
            ),
            self.repository.latest_session(project.id),
            self.repository.list_recent_completed_tasks(project.id, limit=1),
            self.repository.list_doing_tasks(project.id, limit=5),
        )

    def resume(self, project_name: str) -> ResumeContext:
        project, progress, _, recent_completed_tasks, doing_tasks = self.progress(project_name)
        return ResumeContext(
            project=project,
            progress=progress,
            active_tasks=doing_tasks + self.repository.list_tasks(project.id, "todo")[:5],
            notes=self.repository.list_notes(project.id, limit=3),
            decisions=self.repository.list_decisions(project.id, limit=3),
            latest_session=self.repository.latest_session(project.id),
            recent_completed_tasks=recent_completed_tasks,
        )

    def continue_context(self, project_name: str) -> ContinueContext:
        project = self.get_project(self._safe_required(project_name, "Project name"))
        doing_tasks = self.repository.list_doing_tasks(project.id, limit=5)
        todo_tasks = self.repository.list_tasks(project.id, "todo")
        latest_session = self.repository.latest_session(project.id)
        recommended_task = self.repository.choose_recommended_task(project.id, "doing")
        if recommended_task is None:
            recommended_task = self.repository.choose_recommended_task(project.id, "todo")
        if latest_session and latest_session.next_action:
            recommended_next_action = latest_session.next_action
        elif recommended_task:
            recommended_next_action = recommended_task.title
        else:
            recommended_next_action = "No pending action exists."
        return ContinueContext(
            project=project,
            latest_session=latest_session,
            unfinished_tasks=(doing_tasks + todo_tasks)[:5],
            decisions=self.repository.list_decisions(project.id, limit=3),
            recommended_next_action=recommended_next_action,
        )

    @staticmethod
    def _required(value: str, label: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError(f"{label} is required")
        return cleaned

    @classmethod
    def _safe_required(cls, value: str, label: str) -> str:
        cleaned = cls._required(value, label)
        cls._reject_sensitive_content(cleaned)
        return cleaned

    @classmethod
    def _safe_optional(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned:
            cls._reject_sensitive_content(cleaned)
        return cleaned

    @staticmethod
    def _reject_sensitive_content(value: str) -> None:
        if SENSITIVE_CONTENT_PATTERN.search(value):
            raise InvalidMemoryValueError("Sensitive values cannot be stored in Workspace Memory")
