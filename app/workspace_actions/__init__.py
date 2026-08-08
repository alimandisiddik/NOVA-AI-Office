"""Approval-gated, narrow Google Workspace write actions."""

from app.workspace_actions.models import WorkspaceAction
from app.workspace_actions.service import UnavailableDocsWriter, WorkspaceActionService

__all__ = ("UnavailableDocsWriter", "WorkspaceAction", "WorkspaceActionService")
