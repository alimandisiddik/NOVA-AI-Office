"""Sanitized, caller-actionable errors for controlled dispatch operations."""

from __future__ import annotations


class DispatchError(RuntimeError):
    retryable = False


class InvalidRequestError(DispatchError):
    pass


class UnknownAgentError(DispatchError):
    pass


class UnsupportedCapabilityError(DispatchError):
    pass


class ApprovalRequiredError(DispatchError):
    pass


class ApprovalRejectedError(DispatchError):
    pass


class DuplicateDispatchError(DispatchError):
    pass


class StaleUpdateError(DispatchError):
    pass


class DispatchUnavailableError(DispatchError):
    retryable = True


class ExecutionFailureError(DispatchError):
    retryable = True


class DispatchTimeoutError(DispatchError):
    retryable = True


class DispatchNotFoundError(DispatchError):
    pass


class ApprovalNotFoundError(DispatchError):
    pass


class ApprovalAuthorizationError(DispatchError):
    pass


class InvalidTransitionError(DispatchError):
    pass


class CancellationError(DispatchError):
    pass


class RetryExhaustedError(DispatchError):
    pass


# Compatibility alias retained for callers that imported the early draft name.
TimeoutError = DispatchTimeoutError
