"""Workspace Memory use cases independent of Telegram transport."""

from __future__ import annotations

import re

from app.memory.database import MemoryDatabase
from app.memory.models import ContinueContext, ProgressSummary, ResumeContext
from app.memory.repositories import (
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
        return self.repository.update_task_status(task_id, status.strip().lower())

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
        return self.repository.create_session(
            project.id,
            self._safe_required(summary, "Session summary"),
            self._safe_optional(completed_items),
            self._safe_optional(next_action),
            started_at or utc_now(),
            ended_at,
        )

    def progress(self, project_name: str) -> tuple[object, ProgressSummary, object | None]:
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
        )

    def resume(self, project_name: str) -> ResumeContext:
        project, progress, _ = self.progress(project_name)
        return ResumeContext(
            project=project,
            progress=progress,
            active_tasks=self.repository.list_tasks(project.id, "doing")[:5]
            + self.repository.list_tasks(project.id, "todo")[:5],
            notes=self.repository.list_notes(project.id, limit=3),
            decisions=self.repository.list_decisions(project.id, limit=3),
            latest_session=self.repository.latest_session(project.id),
        )

    def continue_context(self, project_name: str) -> ContinueContext:
        project = self.get_project(self._safe_required(project_name, "Project name"))
        unfinished = self.repository.list_tasks(project.id, "doing") + self.repository.list_tasks(project.id, "todo")
        return ContinueContext(
            project=project,
            latest_session=self.repository.latest_session(project.id),
            unfinished_tasks=unfinished[:5],
            decisions=self.repository.list_decisions(project.id, limit=3),
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
