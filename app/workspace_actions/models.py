"""Metadata-only contracts for the Sprint 8E action aggregate."""

from __future__ import annotations

from dataclasses import dataclass

WORKSPACE_ACTION_TYPE = "create_docs_file"
WORKSPACE_ACTION_STATUSES = frozenset({
    "prepared", "awaiting_approval", "approved", "executing", "succeeded",
    "failed", "rejected", "cancelled", "outcome_unknown",
})


@dataclass(frozen=True)
class WorkspaceAction:
    id: int
    action_type: str
    prepared_action_id: int
    dispatch_id: str | None
    approval_id: str | None
    payload_schema_version: int
    payload_fingerprint: str
    account_namespace: str | None
    idempotency_key: str
    correlation_id: str
    requested_chat_id: str | None
    status: str
    version: int
    provider_reference: str | None
    failure_category: str | None
    requested_at: str
    approved_at: str | None
    executing_at: str | None
    completed_at: str | None
    updated_at: str
