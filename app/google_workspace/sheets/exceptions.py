"""Sanitized typed Google Sheets failures."""

from __future__ import annotations


class SheetsServiceError(Exception):
    """Base Google Sheets exception with a stable category and retry hint."""

    category = "provider_failure"
    retryable = False
    message = "Google Sheets operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class SheetsAuthenticationError(SheetsServiceError): category = "authentication"; message = "Google Sheets authentication is required"
class SheetsPermissionError(SheetsServiceError): category = "permission"; message = "Google Sheets permission was denied"
class SheetsNotFoundError(SheetsServiceError): category = "not_found"; message = "Requested Google Sheets resource was not found"
class SheetsRateLimitError(SheetsServiceError): category = "rate_limit"; retryable = True; message = "Google Sheets rate limit was reached"
class SheetsInvalidRequestError(SheetsServiceError): category = "invalid_request"; message = "Google Sheets request is invalid"
class SheetsNetworkError(SheetsServiceError): category = "network"; retryable = True; message = "Google Sheets network request failed"
class SheetsProviderError(SheetsServiceError): category = "provider_failure"; retryable = True; message = "Google Sheets provider request failed"
