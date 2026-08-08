"""Structural safety checks for the isolated Workspace bridge."""

from __future__ import annotations

from pathlib import Path


def test_workspace_bridge_never_uses_client_identity_or_canonical_table_sql() -> None:
    package = Path(__file__).parents[1] / "app" / "workspace_bridge"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))

    assert "client_id" + "_hash" not in source
    for forbidden in ("control_tower_" + "work_items", "knowledge_" + "items", "knowledge_" + "sources"):
        assert forbidden not in source


def test_workspace_bridge_has_no_workspace_mutation_or_bootstrap_edits() -> None:
    root = Path(__file__).parents[1]
    source = "\n".join(path.read_text() for path in (root / "app" / "workspace_bridge").glob("*.py"))

    for forbidden in (".send(", ".modify(", ".trash(", ".insert(", ".update(", ".delete("):
        assert forbidden not in source
    assert "workspace_bridge" not in (root / "app" / "main.py").read_text()
