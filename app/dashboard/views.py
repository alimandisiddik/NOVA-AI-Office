"""Escaped, server-rendered HTML views for the local dashboard."""

from __future__ import annotations

from html import escape
from pathlib import Path
from string import Template

from app.dashboard.service import DashboardSnapshot

_TEMPLATE = Template((Path(__file__).resolve().parents[2] / "templates" / "executive-dashboard.html").read_text())


def render_dashboard(snapshot: DashboardSnapshot) -> str:
    """Render only escaped canonical state; no controls or write forms exist."""
    return _TEMPLATE.substitute(
        generated_at=escape(snapshot.generated_at),
        notices=_notices(snapshot.errors),
        brief=_brief(snapshot),
        projects=_projects(snapshot),
        work=_work(snapshot),
        assignments=_assignments(snapshot),
        knowledge=_knowledge(snapshot),
        system_status=_system_status(snapshot),
        approvals=_approvals(snapshot),
    )


def render_error(message: str) -> str:
    return _TEMPLATE.substitute(
        generated_at="Unavailable", notices=_notices((message,)), brief=_empty("Executive Brief"),
        projects=_empty("Projects"), work=_empty("Active Work"), assignments=_empty("Agent Assignments"),
        knowledge=_empty("Knowledge"), system_status=_empty("System Status"), approvals=_empty("Approval / Blocker Summary"),
    )


def _notices(errors: tuple[str, ...]) -> str:
    return "" if not errors else "<aside class=\"notice\">" + " ".join(escape(error) for error in errors) + "</aside>"


def _empty(label: str) -> str:
    return f"<p class=\"empty\">No {escape(label.lower())} available.</p>"


def _brief(snapshot: DashboardSnapshot) -> str:
    brief = snapshot.morning_brief
    if brief is None:
        return _empty("Executive Brief")
    priorities = _list([item.title for item in brief.active_priorities])
    blockers = _list([item.title for item in brief.blockers])
    actions = _list([brief.next_actions.get(item.item_id, "No next action") for item in brief.active_work])
    return f"<div class=\"brief-grid\"><div><h3>Current priorities</h3>{priorities}</div><div><h3>Blockers / approvals</h3>{blockers}</div><div><h3>Next actions</h3>{actions}</div></div>"


def _projects(snapshot: DashboardSnapshot) -> str:
    if not snapshot.projects:
        return _empty("Projects")
    return "<ul>" + "".join(f"<li>{escape(project.name)} <small>{escape(project.status)}</small></li>" for project in snapshot.projects) + "</ul>"


def _work(snapshot: DashboardSnapshot) -> str:
    if not snapshot.active_work_items:
        return _empty("Active Work")
    rows = "".join(
        "<tr>"
        f"<td>{escape(item.title)}</td><td>{item.priority_score}</td><td>{escape(item.status)}</td>"
        f"<td>{escape(str(item.project_id or '—'))}</td><td>{escape(snapshot.morning_brief.owners.get(item.item_id, 'Unassigned') if snapshot.morning_brief else 'Unassigned')}</td>"
        f"<td>{escape(snapshot.morning_brief.next_actions.get(item.item_id, 'No next action') if snapshot.morning_brief else 'No next action')}</td>"
        "</tr>"
        for item in snapshot.active_work_items
    )
    return "<table><thead><tr><th>Work item</th><th>Priority</th><th>Status</th><th>Project</th><th>Derived owner</th><th>Next action</th></tr></thead><tbody>" + rows + "</tbody></table>"


def _assignments(snapshot: DashboardSnapshot) -> str:
    if not snapshot.agent_assignments:
        return _empty("Active agent assignments")
    return "<ul>" + "".join(
        f"<li><strong>{escape(item.assigned_agent_id)}</strong> — {escape(item.status)}; work item {escape(item.work_item_id)}</li>"
        for item in snapshot.agent_assignments
    ) + "</ul>"


def _knowledge(snapshot: DashboardSnapshot) -> str:
    if not snapshot.knowledge:
        return _empty("Knowledge")
    return "<ul>" + "".join(
        f"<li><strong>{escape(result.item.title)}</strong> — {escape(result.source.title)} ({escape(result.source.source_type)})</li>"
        for result in snapshot.knowledge
    ) + "</ul>"


def _system_status(snapshot: DashboardSnapshot) -> str:
    night_shift = snapshot.night_shift_mode.mode if snapshot.night_shift_mode else "Unavailable"
    return f"<dl><dt>Night Shift</dt><dd>{escape(night_shift)}</dd><dt>Provider availability</dt><dd>{escape(snapshot.provider_health)}</dd></dl>"


def _approvals(snapshot: DashboardSnapshot) -> str:
    values = [f"Approval: {item.requested_action}" for item in snapshot.pending_approvals]
    values.extend(f"Decision: {item.summary}" for item in snapshot.pending_decisions)
    return f"<p><strong>{len(values)} pending item(s)</strong></p>" + _list(values)


def _list(values: list[str]) -> str:
    return _empty("items") if not values else "<ul>" + "".join(f"<li>{escape(value)}</li>" for value in values) + "</ul>"
