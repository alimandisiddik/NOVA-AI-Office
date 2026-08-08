"""Configuration-only capability reporting for the active Workspace connector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.google_workspace.auth import GoogleAuthenticator
from app.google_workspace.scopes import GoogleScope


@dataclass(frozen=True)
class ServiceCapabilityStatus:
    service: str
    available: bool
    reason: str | None


@dataclass(frozen=True)
class WorkspaceCapabilityReport:
    generated_at: str
    connection: dict[str, Any]
    services: tuple[ServiceCapabilityStatus, ...]


_SERVICE_SCOPES = (
    ("gmail", GoogleScope.GMAIL_READONLY.value),
    ("calendar", GoogleScope.CALENDAR_READONLY.value),
    ("drive", GoogleScope.DRIVE_READONLY.value),
    ("docs", GoogleScope.DOCS_READONLY.value),
    ("sheets", GoogleScope.SHEETS_READONLY.value),
    ("slides", GoogleScope.SLIDES_READONLY.value),
)


def get_workspace_capability_report(authenticator: GoogleAuthenticator | None = None) -> WorkspaceCapabilityReport:
    """Report supported reads without constructing clients or making network calls."""
    generated_at = datetime.now(timezone.utc).isoformat()
    if authenticator is None:
        connection: dict[str, Any] = {"is_connected": False, "expired": False, "has_refresh_token": False, "scopes": (), "client_id_hash": "unavailable"}
        services = tuple(ServiceCapabilityStatus(name, False, "not_configured") for name, _ in _SERVICE_SCOPES)
    else:
        connection = authenticator.get_connection_status()
        granted_scopes = set(connection.get("scopes", ()))
        connected = bool(connection.get("is_connected"))
        services = tuple(
            ServiceCapabilityStatus(name, connected and scope in granted_scopes, "ok" if connected and scope in granted_scopes else ("scope_not_granted" if scope not in granted_scopes else "not_configured"))
            for name, scope in _SERVICE_SCOPES
        )
    return WorkspaceCapabilityReport(generated_at, connection, (*services, ServiceCapabilityStatus("contacts", False, "not_implemented")))
