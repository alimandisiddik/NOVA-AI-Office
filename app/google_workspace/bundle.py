"""Plain service container for the Workspace connector integration gate."""

from __future__ import annotations

from dataclasses import dataclass

from app.google_workspace.auth import GoogleAuthenticator
from app.google_workspace.calendar.service import CalendarService
from app.google_workspace.docs.service import DocsService
from app.google_workspace.drive.service import DriveReadService
from app.google_workspace.factory import GoogleClientFactory
from app.google_workspace.gmail.service import GmailService
from app.google_workspace.sheets.service import SheetsService
from app.google_workspace.slides.service import SlidesService


@dataclass(frozen=True)
class WorkspaceConnectorBundle:
    """Read-domain Workspace services sharing one authenticator/factory.

    ``drive`` is optional: it requires an explicit, separately-configured
    folder allowlist (``Settings.google_drive_allowed_folders``) and is
    ``None`` when that allowlist is absent or invalid. Every other read
    service requires only the base OAuth configuration and is always
    present once the bundle itself is constructed — a missing/invalid Drive
    allowlist never disables Gmail/Calendar/Docs/Sheets/Slides reads.
    """

    authenticator: GoogleAuthenticator
    gmail: GmailService
    calendar: CalendarService
    docs: DocsService
    sheets: SheetsService
    slides: SlidesService
    factory: GoogleClientFactory
    drive: DriveReadService | None = None
