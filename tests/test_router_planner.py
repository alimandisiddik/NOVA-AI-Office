"""Tests for the execution-plan generator (app/router/planner.py)."""

import pytest

from app.router.planner import (
    ExecutionPlan,
    format_plan,
    format_route,
    format_router_status,
    generate_plan,
)
from app.router.roles import list_roles
from app.router.workflows import list_workflows


# ---------------------------------------------------------------------------
# generate_plan output shape
# ---------------------------------------------------------------------------


def test_generate_plan_returns_execution_plan() -> None:
    plan = generate_plan("debug the python error")
    assert isinstance(plan, ExecutionPlan)


def test_generate_plan_has_non_empty_workflow() -> None:
    plan = generate_plan("write a strategy for Q3")
    assert plan.workflow.workflow_id in {
        "GENERAL", "STRATEGY", "GOOGLE_WORKSPACE",
        "TECHNICAL", "PRESENTATION", "ACADEMIC", "FAST",
    }


def test_generate_plan_has_at_least_one_primary_role() -> None:
    plan = generate_plan("review the architecture")
    assert len(plan.primary_roles) >= 1


def test_generate_plan_roles_are_not_connected() -> None:
    plan = generate_plan("create a slide deck")
    for role in (*plan.primary_roles, *plan.support_roles):
        assert role.connection_status == "NOT_CONNECTED"


def test_generate_plan_risk_level_is_valid() -> None:
    plan = generate_plan("list files in Google Drive")
    assert plan.risk.risk_level in {"LOW", "MEDIUM", "HIGH"}


def test_generate_plan_approval_mode_is_valid() -> None:
    plan = generate_plan("delete all the logs")
    assert plan.risk.approval_mode in {"NONE", "NOTIFY", "REQUIRED"}


def test_generate_plan_high_risk_message_includes_approval_note() -> None:
    plan = generate_plan("send the report to the client")
    assert plan.risk.approval_mode == "REQUIRED"
    assert any("approval" in note.lower() for note in plan.notes)


def test_generate_plan_not_connected_note_is_present() -> None:
    plan = generate_plan("what is the status")
    assert any("NOT_CONNECTED" in note for note in plan.notes)


def test_generate_plan_empty_message_does_not_raise() -> None:
    plan = generate_plan("")
    assert plan.workflow.workflow_id == "FAST"


def test_generate_plan_is_frozen() -> None:
    plan = generate_plan("hello")
    with pytest.raises((AttributeError, TypeError)):
        plan.message = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# format_plan
# ---------------------------------------------------------------------------


def test_format_plan_contains_workflow_id() -> None:
    plan = generate_plan("refactor the codebase")
    text = format_plan(plan)
    assert plan.workflow.workflow_id in text


def test_format_plan_contains_risk_level() -> None:
    plan = generate_plan("hello NOVA")
    text = format_plan(plan)
    assert plan.risk.risk_level in text


def test_format_plan_contains_primary_role_display_name() -> None:
    plan = generate_plan("review the architecture")
    text = format_plan(plan)
    for role in plan.primary_roles:
        assert role.display_name in text


# ---------------------------------------------------------------------------
# format_route
# ---------------------------------------------------------------------------


def test_format_route_is_compact_string() -> None:
    plan = generate_plan("quick check")
    text = format_route(plan)
    assert isinstance(text, str)
    assert len(text) > 0


def test_format_route_contains_workflow_display_name() -> None:
    plan = generate_plan("create a slide deck")
    text = format_route(plan)
    assert plan.workflow.display_name in text


# ---------------------------------------------------------------------------
# format_router_status
# ---------------------------------------------------------------------------


def test_format_router_status_contains_all_role_ids() -> None:
    text = format_router_status(list_roles(), list_workflows())
    for role in list_roles():
        assert role.role_id in text


def test_format_router_status_contains_all_workflow_ids() -> None:
    text = format_router_status(list_roles(), list_workflows())
    for wf in list_workflows():
        assert wf.workflow_id in text


def test_format_router_status_mentions_not_connected() -> None:
    text = format_router_status(list_roles(), list_workflows())
    assert "NOT_CONNECTED" in text
