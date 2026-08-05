"""Tests for the logical role registry (app/router/roles.py)."""

import pytest

from app.router.roles import RoleNotFoundError, get_role, list_roles

EXPECTED_ROLE_IDS = {
    "CONTROL_TOWER",
    "WORKSPACE_KNOWLEDGE",
    "TECHNICAL_ARCHITECT",
    "EXECUTION_WORKER",
    "FAST_ROUTER",
}


def test_all_expected_roles_are_registered() -> None:
    role_ids = {role.role_id for role in list_roles()}
    assert role_ids == EXPECTED_ROLE_IDS


def test_get_role_returns_correct_provider_label() -> None:
    assert get_role("CONTROL_TOWER").provider_label == "ChatGPT"
    assert get_role("WORKSPACE_KNOWLEDGE").provider_label == "Gemini"
    assert get_role("TECHNICAL_ARCHITECT").provider_label == "Claude"
    assert get_role("EXECUTION_WORKER").provider_label == "Codex"
    assert "lightweight" in get_role("FAST_ROUTER").provider_label.lower()


def test_get_role_is_case_insensitive() -> None:
    role = get_role("control_tower")
    assert role.role_id == "CONTROL_TOWER"


def test_all_roles_have_not_connected_status() -> None:
    for role in list_roles():
        assert role.connection_status == "NOT_CONNECTED", (
            f"Role {role.role_id} must start NOT_CONNECTED"
        )


def test_get_role_raises_for_unknown_role() -> None:
    with pytest.raises(RoleNotFoundError):
        get_role("NONEXISTENT_ROLE")


def test_roles_are_frozen_dataclasses() -> None:
    role = get_role("FAST_ROUTER")
    with pytest.raises((AttributeError, TypeError)):
        role.connection_status = "CONNECTED"  # type: ignore[misc]


def test_list_roles_returns_five_items() -> None:
    assert len(list_roles()) == 5
