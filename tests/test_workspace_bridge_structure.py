"""Structural safety checks for the isolated Workspace bridge."""

from __future__ import annotations

from pathlib import Path


def test_workspace_bridge_never_uses_client_identity_or_canonical_table_sql() -> None:
    package = Path(__file__).parents[1] / "app" / "workspace_bridge"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))

    assert "client_id" + "_hash" not in source
    for forbidden in ("control_tower_" + "work_items", "knowledge_" + "items", "knowledge_" + "sources"):
        assert forbidden not in source


def test_workspace_bridge_has_no_workspace_mutation() -> None:
    root = Path(__file__).parents[1]
    source = "\n".join(path.read_text() for path in (root / "app" / "workspace_bridge").glob("*.py"))

    for forbidden in (".send(", ".modify(", ".trash(", ".insert(", ".update(", ".delete("):
        assert forbidden not in source


def test_workspace_bridge_package_never_reaches_into_shared_bootstrap() -> None:
    """G3 (app/main.py, app/telegram_bot.py) may reference workspace_bridge; the
    reverse must stay false, so the package itself never owns bootstrap wiring."""
    package = Path(__file__).parents[1] / "app" / "workspace_bridge"
    source = "\n".join(path.read_text() for path in package.glob("*.py"))

    for forbidden in ("app.main", "app.telegram_bot", "build_application", "ApplicationBuilder"):
        assert forbidden not in source
