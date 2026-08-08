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
    authenticator: GoogleAuthenticator
    gmail: GmailService
    calendar: CalendarService
    drive: DriveReadService
    docs: DocsService
    sheets: SheetsService
    slides: SlidesService
    factory: GoogleClientFactory
