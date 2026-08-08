"""Data-transfer objects for conversational confirmation state."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Choice:
    index: int
    label: str
    risk_level: str
    action_token: str


@dataclass(frozen=True)
class PendingInteraction:
    interaction_id: str
    chat_id: str
    user_id: int
    source_command: str
    prompt_summary: str
    choices: tuple[Choice, ...]
    max_risk_level: str
    status: str
    resolved_choice_index: int | None
    resolved_at: str | None
    created_at: str
    expires_at: str


@dataclass(frozen=True)
class Resolution:
    outcome: str
    action_token: str | None
    response_text: str
