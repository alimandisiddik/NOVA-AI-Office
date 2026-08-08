"""Immutable, read-only DTOs for Workspace intelligence views."""

from __future__ import annotations

from dataclasses import dataclass

from app.google_workspace.calendar.dtos import MeetingBrief
from app.google_workspace.gmail.dtos import MessageSummary


@dataclass(frozen=True)
class PrioritizedMessage:
    """A source message plus clearly separated inference and recommendation."""

    message: MessageSummary
    priority_score: int
    reason: str
    recommendation: str


@dataclass(frozen=True)
class PrioritizedInboxView:
    """A bounded, freshly computed inbox view."""

    generated_at: str
    messages: tuple[PrioritizedMessage, ...]
    truncated: bool


@dataclass(frozen=True)
class FlaggedMeeting:
    """A source meeting brief plus deterministic attention signals."""

    brief: MeetingBrief
    has_conflict: bool
    missing_preparation: bool


@dataclass(frozen=True)
class AgendaView:
    """A bounded, freshly computed two-day agenda view."""

    generated_at: str
    today: tuple[FlaggedMeeting, ...]
    tomorrow: tuple[FlaggedMeeting, ...]
    truncated: bool = False
