"""Sanitized typed failures for the read-only Gmail connector."""

from __future__ import annotations


class GmailServiceError(Exception):
    category = "provider_failure"
    retryable = False
    message = "Gmail operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class GmailAuthenticationError(GmailServiceError):
    category = "authentication"
    message = "Gmail authentication is required"


class GmailPermissionError(GmailServiceError):
    category = "permission"
    message = "Gmail permission was denied"


class GmailNotFoundError(GmailServiceError):
    category = "not_found"
    message = "Requested Gmail resource was not found"


class GmailRateLimitError(GmailServiceError):
    category = "rate_limit"
    retryable = True
    message = "Gmail rate limit was reached"


class GmailInvalidRequestError(GmailServiceError):
    category = "invalid_request"
    message = "Gmail request is invalid"


class GmailNetworkError(GmailServiceError):
    category = "network"
    retryable = True
    message = "Gmail network request failed"


class GmailProviderError(GmailServiceError):
    category = "provider_failure"
    retryable = True
    message = "Gmail provider request failed"
