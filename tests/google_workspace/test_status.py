"""Tests for non-sensitive Workspace capability reporting."""

from __future__ import annotations

import importlib
from pathlib import Path

from app.google_workspace.docs.exceptions import DocsServiceError
from app.google_workspace.gmail.exceptions import GmailServiceError
from app.google_workspace.scopes import GoogleScope
from app.google_workspace.sheets.exceptions import SheetsServiceError
from app.google_workspace.slides.exceptions import SlidesServiceError
from app.google_workspace.status import get_workspace_capability_report


class ConnectedAuthenticator:
    def get_connection_status(self) -> dict[str, object]:
        return {
            "is_connected": True,
            "expired": False,
            "has_refresh_token": True,
            "scopes": tuple(scope.value for scope in GoogleScope),
            "client_id_hash": "opaque-client",
        }


def test_unconfigured_status_has_exactly_seven_unavailable_services() -> None:
    report = get_workspace_capability_report()
    assert [item.service for item in report.services] == ["gmail", "calendar", "drive", "docs", "sheets", "slides", "contacts"]
    assert all(not item.available for item in report.services)
    assert all(item.reason == "not_configured" for item in report.services[:-1])
    assert report.services[-1].reason == "not_implemented"


def test_configured_status_reports_active_scopes_without_secrets() -> None:
    report = get_workspace_capability_report(ConnectedAuthenticator())  # type: ignore[arg-type]
    assert all(item.available for item in report.services[:-1])
    assert all("token" not in str(item).lower() and "path" not in str(item).lower() for item in report.services)


def test_workspace_packages_and_main_import_without_configuration() -> None:
    assert importlib.import_module("app.main") is not None
    assert get_workspace_capability_report().connection["is_connected"] is False


def test_workspace_source_exposes_no_write_calls_or_deferred_service() -> None:
    workspace_root = Path("app/google_workspace")
    all_source = "\n".join(path.read_text(encoding="utf-8") for path in workspace_root.rglob("*.py"))
    assert "keep" not in all_source.lower()
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for package in ("gmail", "docs", "sheets", "slides")
        for path in (workspace_root / package).rglob("*.py")
    )
    lowered = source.lower()
    for operation in (
        ".send(", ".modify(", ".trash(", ".batchupdate(",
        ".values().append(", ".values().update(", ".values().clear(",
        ".delete(", ".create(", ".patch(", ".insert(",
    ):
        assert operation not in lowered
    assert "app.providers" not in source


def test_new_service_exceptions_do_not_cross_domain_inherit() -> None:
    """Each service's error taxonomy must be independent, mirroring Calendar's template.

    A prior defect chained SlidesServiceError -> DocsServiceError ->
    GmailServiceError, so a bare ``except GmailServiceError`` would silently
    also catch Docs/Sheets/Slides failures.
    """
    assert DocsServiceError.__bases__ == (Exception,)
    assert SheetsServiceError.__bases__ == (Exception,)
    assert SlidesServiceError.__bases__ == (Exception,)
    assert not issubclass(DocsServiceError, GmailServiceError)
    assert not issubclass(SheetsServiceError, GmailServiceError)
    assert not issubclass(SlidesServiceError, GmailServiceError)
    assert not issubclass(SheetsServiceError, DocsServiceError)
    assert not issubclass(SlidesServiceError, DocsServiceError)
