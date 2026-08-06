"""Tests for deterministic provider model registry selection."""

from __future__ import annotations

from app.providers.selection import select_eligible_models


def _closed(_: str) -> bool:
    return False


def test_workflow_based_selection() -> None:
    assert select_eligible_models(
        "TECHNICAL", "TECHNICAL_ARCHITECT", ["nova-v1-fast", "nova-v1"],
        ["nova-v1-fast", "nova-v1"], _closed,
    ) == ["nova-v1"]


def test_role_based_selection() -> None:
    assert select_eligible_models(
        "FAST", "FAST_ROUTER", ["nova-v1", "nova-v1-fast"],
        ["nova-v1", "nova-v1-fast"], _closed,
    ) == ["nova-v1-fast"]


def test_disabled_model_is_skipped() -> None:
    assert select_eligible_models(
        "GENERAL", "CONTROL_TOWER", ["nova-v2-preview", "nova-v1"],
        ["nova-v2-preview", "nova-v1"], _closed,
    ) == ["nova-v1"]


def test_circuit_open_model_is_skipped() -> None:
    assert select_eligible_models(
        "GENERAL", "CONTROL_TOWER", ["nova-v1", "nova-v1-fallback"],
        ["nova-v1", "nova-v1-fallback"], lambda model_id: model_id == "nova-v1",
    ) == ["nova-v1-fallback"]


def test_no_eligible_model_returns_empty_list() -> None:
    assert select_eligible_models(
        "TECHNICAL", "TECHNICAL_ARCHITECT", ["nova-v1-fast"], ["nova-v1-fast"], _closed,
    ) == []


def test_selection_is_deterministic() -> None:
    args = (
        "GENERAL", "CONTROL_TOWER", ["nova-v1", "nova-v1-fallback"],
        ["nova-v1", "nova-v1-fallback"], _closed,
    )
    assert select_eligible_models(*args) == select_eligible_models(*args)


def test_fallback_group_isolation() -> None:
    assert select_eligible_models(
        "GENERAL", "CONTROL_TOWER", ["nova-v1", "nova-v1-isolated", "nova-v1-fallback"],
        ["nova-v1", "nova-v1-isolated", "nova-v1-fallback"], _closed,
    ) == ["nova-v1", "nova-v1-fallback"]
