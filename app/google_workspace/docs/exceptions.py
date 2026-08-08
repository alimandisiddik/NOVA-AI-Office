"""Sanitized typed Google Docs failures."""

from __future__ import annotations


class DocsServiceError(Exception):
    """Base Google Docs exception with a stable category and retry hint."""

    category = "provider_failure"
    retryable = False
    message = "Google Docs operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class DocsAuthenticationError(DocsServiceError):
    category = "authentication"
    message = "Google Docs authentication is required"


class DocsPermissionError(DocsServiceError):
    category = "permission"
    message = "Google Docs permission was denied"


class DocsNotFoundError(DocsServiceError):
    category = "not_found"
    message = "Requested Google Docs resource was not found"


class DocsRateLimitError(DocsServiceError):
    category = "rate_limit"
    retryable = True
    message = "Google Docs rate limit was reached"


class DocsInvalidRequestError(DocsServiceError):
    category = "invalid_request"
    message = "Google Docs request is invalid"


class DocsNetworkError(DocsServiceError):
    category = "network"
    retryable = True
    message = "Google Docs network request failed"


class DocsProviderError(DocsServiceError):
    category = "provider_failure"
    retryable = True
    message = "Google Docs provider request failed"
