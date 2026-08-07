"""Exception types and error categories for the provider gateway."""

from __future__ import annotations


class ProviderError(Exception):
    """Base exception for provider gateway errors."""

    category: str = "provider_error"

    def __init__(self, message: str, category: str | None = None) -> None:
        super().__init__(message)
        if category:
            self.category = category


class ConfigurationError(ProviderError):
    category = "configuration_error"


class AuthenticationError(ProviderError):
    category = "authentication_error"


class AuthorizationError(ProviderError):
    category = "authorization_error"


class RateLimitError(ProviderError):
    category = "rate_limit_error"


class TimeoutError(ProviderError):
    category = "timeout_error"


class ConnectionError(ProviderError):
    category = "connection_error"


class InvalidResponseError(ProviderError):
    category = "invalid_response"


class OutputLimitError(ProviderError):
    category = "output_limit_error"


class SensitiveContentError(ProviderError):
    category = "sensitive_content_error"


class UnsupportedOperationError(ProviderError):
    category = "unsupported_operation"


class CircuitOpenError(ProviderError):
    category = "circuit_open"

class ProviderCancelledError(ProviderError):
    category = "cancelled"
