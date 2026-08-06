"""Sanitized, typed errors for Calendar integration failures."""

from __future__ import annotations


class CalendarServiceError(Exception):
    """Base Calendar exception with a stable category and retry hint."""

    category = "provider_failure"
    retryable = False
    message = "Calendar operation failed"

    def __init__(self) -> None:
        super().__init__(self.message)


class CalendarDependencyError(CalendarServiceError):
    category = "dependency"
    message = "Calendar integration dependency is unavailable"


class CalendarConfigurationError(CalendarServiceError):
    category = "configuration"
    message = "Calendar integration configuration is invalid"


class CalendarAuthenticationError(CalendarServiceError):
    category = "authentication"
    message = "Google Calendar authentication is required"


class CalendarPermissionError(CalendarServiceError):
    category = "permission"
    message = "Google Calendar permission was denied"


class CalendarRateLimitError(CalendarServiceError):
    category = "rate_limit"
    retryable = True
    message = "Google Calendar rate limit was reached"


class CalendarNotFoundError(CalendarServiceError):
    category = "not_found"
    message = "Requested calendar resource was not found"


class CalendarNetworkError(CalendarServiceError):
    category = "network"
    retryable = True
    message = "Google Calendar network request failed"


class CalendarInvalidRequestError(CalendarServiceError):
    category = "invalid_request"
    message = "Calendar request is invalid"


class CalendarProviderError(CalendarServiceError):
    category = "provider_failure"
    retryable = True
    message = "Google Calendar provider request failed"


class CalendarAuditError(CalendarServiceError):
    category = "audit"
    message = "Calendar audit persistence failed"
