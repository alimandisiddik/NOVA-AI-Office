"""Tests for the workflow registry (app/router/workflows.py)."""

import pytest

from app.router.workflows import WorkflowNotFoundError, get_workflow, list_workflows

EXPECTED_WORKFLOW_IDS = {
    "GENERAL",
    "STRATEGY",
    "GOOGLE_WORKSPACE",
    "TECHNICAL",
    "PRESENTATION",
    "ACADEMIC",
    "FAST",
}


def test_all_expected_workflows_are_registered() -> None:
    wf_ids = {wf.workflow_id for wf in list_workflows()}
    assert wf_ids == EXPECTED_WORKFLOW_IDS


def test_get_workflow_returns_correct_workflow() -> None:
    wf = get_workflow("TECHNICAL")
    assert wf.workflow_id == "TECHNICAL"
    assert "TECHNICAL_ARCHITECT" in wf.primary_roles


def test_get_workflow_is_case_insensitive() -> None:
    wf = get_workflow("general")
    assert wf.workflow_id == "GENERAL"


def test_get_workflow_raises_for_unknown_workflow() -> None:
    with pytest.raises(WorkflowNotFoundError):
        get_workflow("UNKNOWN_WORKFLOW")


def test_every_workflow_has_at_least_one_primary_role() -> None:
    for wf in list_workflows():
        assert len(wf.primary_roles) >= 1, f"{wf.workflow_id} has no primary roles"


def test_fast_workflow_has_fast_router_as_primary() -> None:
    wf = get_workflow("FAST")
    assert "FAST_ROUTER" in wf.primary_roles


def test_workflows_are_frozen_dataclasses() -> None:
    wf = get_workflow("GENERAL")
    with pytest.raises((AttributeError, TypeError)):
        wf.workflow_id = "MODIFIED"  # type: ignore[misc]


def test_list_workflows_returns_seven_items() -> None:
    assert len(list_workflows()) == 7
