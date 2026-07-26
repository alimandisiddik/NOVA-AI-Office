"""Domain models for NOVA Workspace Memory."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Project:
    id: int
    name: str
    description: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Task:
    id: int
    project_id: int
    title: str
    description: str
    status: str
    priority: str
    due_date: str | None
    created_at: str
    updated_at: str
    completed_at: str | None


@dataclass(frozen=True)
class TaskStatusUpdate:
    task: Task
    previous_status: str


@dataclass(frozen=True)
class Note:
    id: int
    project_id: int
    content: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Decision:
    id: int
    project_id: int
    decision: str
    reason: str | None
    created_at: str


@dataclass(frozen=True)
class WorkSession:
    id: int
    project_id: int
    summary: str
    completed_items: str
    next_action: str
    started_at: str
    ended_at: str | None
    created_at: str


@dataclass(frozen=True)
class ProgressSummary:
    total: int
    todo: int
    doing: int
    done: int
    cancelled: int
    completion_percentage: int


@dataclass(frozen=True)
class ResumeContext:
    project: Project
    progress: ProgressSummary
    active_tasks: list[Task]
    notes: list[Note]
    decisions: list[Decision]
    latest_session: WorkSession | None
    recent_completed_tasks: list[Task]


@dataclass(frozen=True)
class ContinueContext:
    project: Project
    latest_session: WorkSession | None
    unfinished_tasks: list[Task]
    decisions: list[Decision]
    recommended_next_action: str
