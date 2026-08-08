"""Typed errors for the AgentAssignment state machine."""

from __future__ import annotations


class AgentAssignmentError(RuntimeError):
    """Base error for assignment operations."""


class AssignmentNotFoundError(AgentAssignmentError):
    """Raised when an assignment does not exist."""


class AssignmentTransitionError(AgentAssignmentError):
    """Raised when an assignment state transition is not allowed."""


class AssignmentValidationError(AgentAssignmentError):
    """Raised when assignment input is invalid or unsafe."""


class AssignmentStaleUpdateError(AgentAssignmentError):
    """Raised when a compare-and-swap assignment update loses a race."""
