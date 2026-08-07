"""Typed, metadata-only models for NOVA's night-shift foundation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeModeState:
    mode: str
    is_manual_override: bool
    updated_at: str


@dataclass(frozen=True)
class NightSchedule:
    start_time: str
    end_time: str
    morning_brief_time: str
    timezone: str
    updated_at: str


@dataclass(frozen=True)
class NightJobClassification:
    job_type: str
    category: str
    disposition: str


@dataclass(frozen=True)
class NightQueueJob:
    id: int
    job_id: str
    job_type: str
    category: str
    status: str
    requested_at: str
    eligible_after: str | None
    completed_at: str | None
    failure_category: str | None
    approval_required: bool
    deduplication_key: str
    audit_metadata: str
    dispatch_id: str | None = None
    lease_worker_id: str | None = None
    lease_expires_at: str | None = None
    attempt_count: int = 0
    approval_id: str | None = None


@dataclass(frozen=True)
class NotificationEvent:
    id: int
    event_type: str
    severity: str
    routing: str
    summary: str
    related_job_id: int | None
    created_at: str


@dataclass(frozen=True)
class MorningBrief:
    id: int
    briefing_window: str
    version: int
    completed_overnight: str
    attention_required: str
    awaiting_approval: str
    failed_safely: str
    runtime_health: str
    generated_at: str
