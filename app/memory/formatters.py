"""Concise Telegram-safe renderers for Workspace Memory records."""

from __future__ import annotations

from collections import defaultdict

from app.memory.models import ContinueContext, ProgressSummary, ResumeContext, Task

TASK_MARKERS = {"todo": "☐", "doing": "◐", "done": "✓", "cancelled": "×"}


def projects_message(projects) -> str:
    if not projects:
        return "Belum ada project. Buat dengan: /project Nama Project | deskripsi"
    grouped = defaultdict(list)
    for project in projects:
        grouped[project.status].append(project.name)
    lines = ["Projects"]
    for status in ("active", "paused", "completed", "archived"):
        if grouped[status]:
            lines.append(f"\n{status.upper()}")
            lines.extend(f"• {name}" for name in grouped[status])
    return "\n".join(lines)


def tasks_message(project, tasks) -> str:
    if not tasks:
        return f"Tidak ada task untuk {project.name}. Buat dengan: /task {project.name} | judul task"
    lines = [f"Tasks — {project.name}"]
    for task in tasks:
        lines.append(f"{TASK_MARKERS[task.status]} [{task.status}] {task.title} ({task.priority})")
    return "\n".join(lines)


def progress_message(project, progress: ProgressSummary, latest_session) -> str:
    lines = [
        f"Progress — {project.name}",
        f"Total: {progress.total}",
        f"Todo: {progress.todo} | Doing: {progress.doing} | Done: {progress.done} | Cancelled: {progress.cancelled}",
        f"Completion: {progress.completion_percentage}%",
    ]
    if latest_session and latest_session.next_action:
        lines.append(f"Next action: {latest_session.next_action}")
    return "\n".join(lines)


def resume_message(context: ResumeContext) -> str:
    lines = [
        f"Resume — {context.project.name}",
        f"Status: {context.project.status}",
        f"Tasks: {context.progress.done}/{context.progress.total} done ({context.progress.completion_percentage}%)",
    ]
    if context.active_tasks:
        lines.append("Active tasks: " + "; ".join(task.title for task in context.active_tasks[:5]))
    if context.notes:
        lines.append("Recent notes: " + " | ".join(note.content for note in context.notes[:3]))
    if context.decisions:
        lines.append("Recent decisions: " + " | ".join(decision.decision for decision in context.decisions[:3]))
    if context.latest_session:
        lines.append(f"Latest session: {context.latest_session.summary}")
        if context.latest_session.next_action:
            lines.append(f"Next action: {context.latest_session.next_action}")
    return "\n".join(lines)


def continue_message(context: ContinueContext) -> str:
    lines = [f"Continue — {context.project.name}"]
    if context.latest_session:
        lines.append(f"Last session: {context.latest_session.summary}")
        if context.latest_session.completed_items:
            lines.append(f"Completed: {context.latest_session.completed_items}")
    else:
        lines.append("No work session recorded yet.")
    if context.unfinished_tasks:
        lines.append("Unfinished: " + "; ".join(task.title for task in context.unfinished_tasks[:5]))
    if context.decisions:
        lines.append("Latest decisions: " + " | ".join(item.decision for item in context.decisions[:3]))
    if context.latest_session and context.latest_session.next_action:
        lines.append(f"Recommended next action: {context.latest_session.next_action}")
    elif context.unfinished_tasks:
        lines.append(f"Recommended next action: {context.unfinished_tasks[0].title}")
    return "\n".join(lines)
