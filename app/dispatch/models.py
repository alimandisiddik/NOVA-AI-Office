"""Frozen data-transfer objects for controlled dispatch operations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchRequest:
    source_type: str
    source_id: str
    agent_id: str
    capability: str
    payload_ref: str
    idempotency_key: str
    requested_by: str
    correlation_id: str | None = None
    max_attempts: int = 3


@dataclass(frozen=True)
class DispatchRecord:
    dispatch_id: str
    source_type: str
    source_id: str
    agent_id: str
    capability: str
    payload_ref: str
    status: str
    attempt_count: int
    max_attempts: int
    idempotency_key: str
    correlation_id: str | None
    requested_by: str
    result_summary: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DispatchResult:
    dispatch_id: str
    attempt_number: int
    success: bool
    summary: str
    ended_at: str
    timed_out: bool = False


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    dispatch_id: str
    requested_action: str
    status: str
    requested_by: str
    requested_at: str
    resolved_by: str | None = None
    resolved_at: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True)
class ApprovalDecision:
    approval_id: str
    dispatch_id: str
    status: str
    resolved_by: str
    resolved_at: str


@dataclass(frozen=True)
class CancellationResult:
    dispatch_id: str
    status: str
    cancelled_at: str


@dataclass(frozen=True)
class StatusSyncResult:
    dispatch_id: str
    applied: bool
    status: str
    updated_at: str
