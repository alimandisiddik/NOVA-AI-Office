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
    # The upstream/provider route identity to actually send (e.g. 9Router's
    # "general" combo) -- distinct from ``model_id`` (the NOVA-internal
    # alias, e.g. "nova-v1"), which an adapter must never send upstream.
    # ``None`` for providers where this concept does not apply.
    upstream_route_id: Optional[str] = None


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
    provider_id: str = "9Router"
    # The upstream/provider route identity this attempt targeted, distinct
    # from ``model_id`` (the NOVA-internal alias). ``None`` if not
    # applicable to this provider or if the attempt was skipped before an
    # upstream route was ever resolved.
    upstream_route_id: Optional[str] = None


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
    initial_provider_id: Optional[str] = None
    final_provider_id: Optional[str] = None
    resolved_model_label: Optional[str] = None
    # Three distinct identities, all separately auditable (Sprint 5G.1):
    # initial/final_model_id = NOVA-internal alias (e.g. "nova-v1"),
    # initial/final_upstream_route_id = provider/combo route actually
    # dispatched to (e.g. "general"), resolved_model_label = what the
    # provider itself reports it used (e.g. "gemini-pro-default").
    initial_upstream_route_id: Optional[str] = None
    final_upstream_route_id: Optional[str] = None
