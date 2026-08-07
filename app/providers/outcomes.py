"""Typed, deterministic outcome taxonomy for provider execution."""

from __future__ import annotations

from enum import Enum

from app.providers.errors import (
    AuthenticationError,
    AuthorizationError,
    CircuitOpenError,
    ConnectionError,
    InvalidResponseError,
    OutputLimitError,
    ProviderCancelledError,
    ProviderError,
    RateLimitError,
    TimeoutError,
    UnsupportedOperationError,
)


class ProviderOutcome(str, Enum):
    SUCCESS = "success"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AUTHENTICATION_FAILED = "authentication_failed"
    INVALID_REQUEST = "invalid_request"
    MODEL_ERROR = "model_error"
    CANCELLED = "cancelled"


FALLBACK_ELIGIBLE = frozenset(
    {
        ProviderOutcome.QUOTA_EXHAUSTED,
        ProviderOutcome.RATE_LIMITED,
        ProviderOutcome.TIMEOUT,
        ProviderOutcome.PROVIDER_UNAVAILABLE,
    }
)


def outcome_for_error(error: ProviderError) -> ProviderOutcome:
    """Classify a sanitized provider error by type, never by message content.

    ``quota_exhausted`` and ``rate_limited`` are both fallback-eligible, so
    they have no behavioral difference today; splitting them would require a
    real, typed quota-exhaustion signal from an adapter (e.g. a dedicated
    exception raised from a specific, evidenced API response shape), not a
    heuristic match against an error message that adapters make no contract
    to keep stable. Until such a typed signal exists, every ``RateLimitError``
    is classified ``rate_limited``.
    """
    if isinstance(error, RateLimitError):
        return ProviderOutcome.RATE_LIMITED
    if isinstance(error, TimeoutError):
        return ProviderOutcome.TIMEOUT
    if isinstance(error, (ConnectionError, CircuitOpenError)):
        return ProviderOutcome.PROVIDER_UNAVAILABLE
    if isinstance(error, (AuthenticationError, AuthorizationError)):
        return ProviderOutcome.AUTHENTICATION_FAILED
    if isinstance(error, UnsupportedOperationError):
        return ProviderOutcome.INVALID_REQUEST
    if isinstance(error, ProviderCancelledError):
        return ProviderOutcome.CANCELLED
    if isinstance(error, (InvalidResponseError, OutputLimitError)):
        return ProviderOutcome.MODEL_ERROR
    return ProviderOutcome.MODEL_ERROR


def is_fallback_eligible(outcome: ProviderOutcome) -> bool:
    """Return whether the policy permits continuing to a fallback candidate."""
    return outcome in FALLBACK_ELIGIBLE
