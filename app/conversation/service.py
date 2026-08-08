"""Confirmation state machine for context-aware Telegram replies."""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from app.conversation.models import Choice, PendingInteraction, Resolution
from app.conversation.repository import ConversationRepository, utc_now
from app.conversation.schema import apply_schema
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.security import SENSITIVE_CONTENT_PATTERN

_CONTEXTUAL_REPLIES = frozenset({"lanjut", "lanjutkan", "teruskan", "oke", "ok", "ya", "setuju"})
_TRIMMED_PUNCTUATION = re.compile(r"^[\s\.,!?;:]+|[\s\.,!?;:]+$")
_MAX_TEXT_LENGTH = 500


class ConversationValidationError(ValueError):
    """Raised when a caller provides unsafe or malformed interaction data."""


class ConversationService:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database
        self.repository = ConversationRepository(database)

    def initialize(self) -> None:
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
        except sqlite3.Error as error:
            raise MemoryDatabaseError("Conversation schema initialization failed") from error

    def ask(
        self,
        chat_id: str,
        user_id: int,
        source_command: str,
        prompt_summary: str,
        choices: list[Choice],
        ttl_seconds: int = 600,
    ) -> PendingInteraction:
        safe_chat_id = self._safe_text(chat_id, "chat id")
        safe_user_id = self._safe_user_id(user_id)
        safe_source_command = self._safe_text(source_command, "source command")
        safe_prompt_summary = self._safe_text(prompt_summary, "prompt summary")
        safe_choices = self._safe_choices(choices)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 86400:
            raise ConversationValidationError("Interaction expiry is invalid.")
        now = datetime.now(timezone.utc)
        interaction = PendingInteraction(
            interaction_id=str(uuid.uuid4()),
            chat_id=safe_chat_id,
            user_id=safe_user_id,
            source_command=safe_source_command,
            prompt_summary=safe_prompt_summary,
            choices=safe_choices,
            max_risk_level="high" if any(choice.risk_level == "high" for choice in safe_choices) else "low",
            status="open",
            resolved_choice_index=None,
            resolved_at=None,
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
        )
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.repository.expire_open_in(connection, utc_now())
            existing = self.repository.get_open_for_chat_in(connection, safe_chat_id)
            if existing is not None:
                self.repository.transition_in(
                    connection,
                    existing.interaction_id,
                    "superseded",
                    f"user:{safe_user_id}",
                    event="superseded",
                )
            self.repository.insert_in(connection, interaction, f"user:{safe_user_id}")
        return interaction

    def try_resolve_pending(self, user_id: int, reply_text: str) -> Resolution | None:
        if isinstance(user_id, bool) or not isinstance(user_id, int):
            return None
        exact_reply = reply_text.strip().casefold() if isinstance(reply_text, str) else ""
        contextual_reply = self._normalize_contextual_reply(reply_text)
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            interaction_before_expiry = self.repository.get_open_for_user_in(connection, user_id)
            now = utc_now()
            self.repository.expire_open_in(connection, now)
            if interaction_before_expiry is not None and interaction_before_expiry.expires_at <= now:
                return Resolution("expired", None, "That request expired. Please resend it.")
            interaction = self.repository.get_open_for_user_in(connection, user_id)
            if interaction is None:
                return None
            if interaction.user_id != user_id:
                return Resolution("wrong_user", None, "This request belongs to another user.")
            choice = self._exact_choice(interaction, exact_reply)
            if choice is not None:
                if not self.repository.transition_in(
                    connection,
                    interaction.interaction_id,
                    "resolved",
                    f"user:{user_id}",
                    resolved_choice_index=choice.index,
                    event="resolved",
                    detail=f"choice:{choice.index}",
                ):
                    return None
                return Resolution("resolved", choice.action_token, f"Selected: {choice.label}")
            if contextual_reply in _CONTEXTUAL_REPLIES and interaction.max_risk_level == "low":
                choice = interaction.choices[0]
                if not self.repository.transition_in(
                    connection,
                    interaction.interaction_id,
                    "resolved",
                    f"user:{user_id}",
                    resolved_choice_index=choice.index,
                    event="resolved",
                    detail=f"choice:{choice.index}",
                ):
                    return None
                return Resolution("resolved", choice.action_token, f"Selected: {choice.label}")
            return Resolution("ambiguous", None, self._reask_text(interaction))

    def expire_stale(self) -> int:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self.repository.expire_open_in(connection, utc_now())

    @staticmethod
    def _safe_user_id(user_id: int) -> int:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 0:
            raise ConversationValidationError("Interaction user is invalid.")
        return user_id

    @staticmethod
    def _safe_text(value: str, field: str) -> str:
        if not isinstance(value, str):
            raise ConversationValidationError(f"Interaction {field} is invalid.")
        cleaned = value.strip()
        if not cleaned or len(cleaned) > _MAX_TEXT_LENGTH or SENSITIVE_CONTENT_PATTERN.search(cleaned):
            raise ConversationValidationError(f"Interaction {field} is invalid.")
        return cleaned

    def _safe_choices(self, choices: list[Choice]) -> tuple[Choice, ...]:
        if not isinstance(choices, list) or not 1 <= len(choices) <= 20:
            raise ConversationValidationError("Interaction choices are invalid.")
        safe_choices: list[Choice] = []
        seen_indexes: set[int] = set()
        for choice in choices:
            if not isinstance(choice, Choice):
                raise ConversationValidationError("Interaction choices are invalid.")
            if isinstance(choice.index, bool) or not isinstance(choice.index, int) or choice.index < 1:
                raise ConversationValidationError("Interaction choice index is invalid.")
            if choice.index in seen_indexes or choice.risk_level not in {"low", "high"}:
                raise ConversationValidationError("Interaction choices are invalid.")
            if choice.risk_level == "high" and self._normalize_contextual_reply(choice.label) in _CONTEXTUAL_REPLIES:
                raise ConversationValidationError(
                    "High-risk choice labels must be action-specific, not a bare contextual word."
                )
            safe_choices.append(
                Choice(
                    index=choice.index,
                    label=self._safe_text(choice.label, "choice label"),
                    risk_level=choice.risk_level,
                    action_token=self._safe_text(choice.action_token, "action token"),
                )
            )
            seen_indexes.add(choice.index)
        return tuple(safe_choices)

    @staticmethod
    def _normalize_contextual_reply(reply_text: str) -> str:
        if not isinstance(reply_text, str):
            return ""
        return _TRIMMED_PUNCTUATION.sub("", reply_text).casefold()

    @staticmethod
    def _exact_choice(interaction: PendingInteraction, normalized_reply: str) -> Choice | None:
        for choice in interaction.choices:
            if normalized_reply == str(choice.index) or normalized_reply == choice.label.casefold():
                return choice
        return None

    @staticmethod
    def _reask_text(interaction: PendingInteraction) -> str:
        options = "\n".join(f"{choice.index}. {choice.label}" for choice in interaction.choices)
        if interaction.max_risk_level == "high":
            return f"Please reply with the exact numbered choice to confirm:\n{options}"
        return f"Please choose one of the listed options:\n{options}"
