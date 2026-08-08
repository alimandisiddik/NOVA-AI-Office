"""Immutable records owned by the Workspace-to-NOVA bridge."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceSourceRef:
    """A reviewable internal reference to one stable Workspace object."""

    id: int
    source_system: str
    account_namespace: str
    external_source_type: str
    external_source_id: str
    content_fingerprint: str | None
    candidate_summary: str
    target_type: str | None
    target_id: str | None
    status: str
    created_by: str
    created_at: str
    updated_at: str
