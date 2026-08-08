"""Read-only Google Docs connector seam."""

from app.google_workspace.docs.service import DocsService

__all__ = ["DocsService"]
from app.google_workspace.docs.service import DocsService, DocsWriteService, LazyDocsWriteService

__all__ = ("DocsService", "DocsWriteService", "LazyDocsWriteService")
