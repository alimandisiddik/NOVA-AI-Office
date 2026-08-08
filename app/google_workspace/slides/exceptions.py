"""Sanitized typed Google Slides failures."""

from __future__ import annotations


class SlidesServiceError(Exception):
    """Base Google Slides exception with a stable category and retry hint."""

    category = "provider_failure"
    retryable = False
    message = "Google Slides operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)
class SlidesAuthenticationError(SlidesServiceError): category = "authentication"; message = "Google Slides authentication is required"
class SlidesPermissionError(SlidesServiceError): category = "permission"; message = "Google Slides permission was denied"
class SlidesNotFoundError(SlidesServiceError): category = "not_found"; message = "Requested Google Slides resource was not found"
class SlidesRateLimitError(SlidesServiceError): category = "rate_limit"; retryable = True; message = "Google Slides rate limit was reached"
class SlidesInvalidRequestError(SlidesServiceError): category = "invalid_request"; message = "Google Slides request is invalid"
class SlidesNetworkError(SlidesServiceError): category = "network"; retryable = True; message = "Google Slides network request failed"
class SlidesProviderError(SlidesServiceError): category = "provider_failure"; retryable = True; message = "Google Slides provider request failed"
