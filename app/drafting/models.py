"""Local-only data contracts for prepared Workspace content."""

from __future__ import annotations

from dataclasses import dataclass


PREPARED_WORKSPACE_ACTION_TYPES = frozenset(
    {"gmail_reply", "gmail_new", "docs_memo", "sheets_change", "slides_outline"}
)
PREPARED_WORKSPACE_ACTION_STATUSES = frozenset({"prepared", "ready_for_action", "superseded"})


@dataclass(frozen=True)
class PreparedWorkspaceAction:
    """A bounded, local draft that cannot perform an external action itself."""

    id: int
    content_type: str
    source_ref: str | None
    title: str | None
    body_text: str | None
    body_payload: str | None
    status: str
    supersedes_id: int | None
    created_by: str
    created_at: str
    updated_at: str
