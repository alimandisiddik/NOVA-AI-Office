"""Explicit, provenance-preserving promotion of Workspace signals."""

from app.workspace_bridge.models import WorkspaceSourceRef
from app.workspace_bridge.service import WorkspaceBridgeService

__all__ = ("WorkspaceBridgeService", "WorkspaceSourceRef")
