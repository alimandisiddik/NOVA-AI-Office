"""Domain models for the provider gateway."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProviderRequest:
    request_id: str
    user_id: int
    provider_id: str
    model_id: str
    workflow_id: str
    role_id: str
    prompt: str
    execution_id: Optional[int] = None


@dataclass(frozen=True)
class ProviderResponse:
    request_id: str
    content: str
    model_id: str
    usage_prompt_tokens: int = 0
    usage_completion_tokens: int = 0
    usage_total_tokens: int = 0


@dataclass(frozen=True)
class ProviderRequestAttempt:
    attempt_number: int
    model_id: str
    latency_ms: int
    error_category: Optional[str]
    status: str
    created_at: str


@dataclass(frozen=True)
class ProviderAuditRecord:
    request_id: str
    execution_id: Optional[int]
    user_id: int
    provider_id: str
    model_id: str
    workflow_id: str
    role_id: str
    status: str
    prompt_hash: str
    response_size: int
    latency_ms: int
    retry_count: int
    error_category: Optional[str]
    created_at: str
    completed_at: str
    initial_model_id: Optional[str] = None
    final_model_id: Optional[str] = None
    attempt_count: int = 1
    fallback_used: int = 0
    fallback_reason: Optional[str] = None
