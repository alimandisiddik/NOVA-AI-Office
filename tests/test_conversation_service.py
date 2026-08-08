from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from app.conversation import Choice, ConversationService
from app.conversation.service import ConversationValidationError
from app.memory.database import MemoryDatabase


def service(tmp_path: Path) -> ConversationService:
    conversation = ConversationService(MemoryDatabase(tmp_path / "conversation.sqlite3"))
    conversation.initialize()
    return conversation


def choices(*, high_risk: bool = False) -> list[Choice]:
    return [
        Choice(1, "Continue", "low", "continue-token"),
        Choice(2, "Review", "low", "review-token"),
        Choice(3, "Stop", "high" if high_risk else "low", "stop-token"),
    ]


@pytest.mark.parametrize("reply", ["1", "2", "3"])
def test_numbered_choices_resolve_exact_active_interaction(tmp_path: Path, reply: str) -> None:
    conversation = service(tmp_path)
    interaction = conversation.ask("chat-1", 7, "/test", "Choose a safe action", choices())

    result = conversation.try_resolve_pending(7, reply)

    assert result is not None
    assert result.outcome == "resolved"
    assert result.action_token == interaction.choices[int(reply) - 1].action_token


@pytest.mark.parametrize("reply", ["lanjut", "lanjutkan", "teruskan", "oke", "ok", "ya", "setuju"])
def test_contextual_replies_resolve_low_risk_interaction(tmp_path: Path, reply: str) -> None:
    conversation = service(tmp_path)
    conversation.ask("chat-1", 7, "/test", "Choose a safe action", choices())

    result = conversation.try_resolve_pending(7, f"  {reply}! ")

    assert result is not None
    assert result.outcome == "resolved"
    assert result.action_token == "continue-token"


def test_ambiguous_reply_reasks_without_consuming_interaction(tmp_path: Path) -> None:
    conversation = service(tmp_path)
    conversation.ask("chat-1", 7, "/test", "Choose a safe action", choices())

    result = conversation.try_resolve_pending(7, "maybe")

    assert result is not None
    assert result.outcome == "ambiguous"
    assert "1. Continue" in result.response_text
    assert conversation.try_resolve_pending(7, "2").action_token == "review-token"


def test_stale_and_expired_choice_cannot_resolve(tmp_path: Path) -> None:
    conversation = service(tmp_path)
    interaction = conversation.ask("chat-1", 7, "/test", "Choose a safe action", choices(), ttl_seconds=1)
    with conversation.database.connection() as connection:
        connection.execute(
            "UPDATE conversation_pending_interactions SET expires_at = ? WHERE interaction_id = ?",
            ("2000-01-01T00:00:00+00:00", interaction.interaction_id),
        )

    expired = conversation.try_resolve_pending(7, "1")

    assert expired is not None
    assert expired.outcome == "expired"
    assert conversation.try_resolve_pending(7, "1") is None


def test_wrong_user_cannot_resolve_another_users_interaction(tmp_path: Path) -> None:
    conversation = service(tmp_path)
    conversation.ask("chat-1", 7, "/test", "Choose a safe action", choices())

    assert conversation.try_resolve_pending(99, "1") is None
    assert conversation.try_resolve_pending(7, "1").outcome == "resolved"


def test_replayed_number_and_duplicate_delivery_resolve_exactly_once(tmp_path: Path) -> None:
    conversation = service(tmp_path)
    conversation.ask("chat-1", 7, "/test", "Choose a safe action", choices())

    first = conversation.try_resolve_pending(7, "1")
    second = conversation.try_resolve_pending(7, "1")

    assert first is not None
    assert first.outcome == "resolved"
    assert second is None


def test_newer_prompt_supersedes_older_prompt(tmp_path: Path) -> None:
    conversation = service(tmp_path)
    older = conversation.ask("chat-1", 7, "/test", "Older safe action", choices())
    newer = conversation.ask(
        "chat-1",
        7,
        "/test",
        "Newer safe action",
        [Choice(1, "Continue new", "low", "new-token")],
    )

    result = conversation.try_resolve_pending(7, "1")
    with conversation.database.connection() as connection:
        old_status = connection.execute(
            "SELECT status FROM conversation_pending_interactions WHERE interaction_id = ?",
            (older.interaction_id,),
        ).fetchone()["status"]

    assert newer.interaction_id != older.interaction_id
    assert old_status == "superseded"
    assert result is not None
    assert result.action_token == "new-token"


def test_high_risk_bare_contextual_reply_is_rejected_but_exact_number_resolves(tmp_path: Path) -> None:
    conversation = service(tmp_path)
    conversation.ask("chat-1", 7, "/test", "Confirm publish", choices(high_risk=True))

    rejected = conversation.try_resolve_pending(7, "oke")
    accepted = conversation.try_resolve_pending(7, "3")

    assert rejected is not None
    assert rejected.outcome == "ambiguous"
    assert "exact numbered choice" in rejected.response_text
    assert accepted is not None
    assert accepted.outcome == "resolved"
    assert accepted.action_token == "stop-token"


def test_exact_high_risk_label_must_match_without_punctuation_relaxation(tmp_path: Path) -> None:
    conversation = service(tmp_path)
    conversation.ask(
        "chat-1",
        7,
        "/test",
        "Confirm a high-risk action",
        [Choice(1, "Publish report", "high", "publish-token")],
    )

    assert conversation.try_resolve_pending(7, "Publish report.").outcome == "ambiguous"
    assert conversation.try_resolve_pending(7, "publish report").action_token == "publish-token"


def test_partial_unique_index_enforces_one_open_interaction_per_chat(tmp_path: Path) -> None:
    conversation = service(tmp_path)
    conversation.ask("chat-1", 7, "/test", "Choose a safe action", choices())

    with conversation.database.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO conversation_pending_interactions (
                    interaction_id, chat_id, user_id, source_command, prompt_summary, choices_json,
                    max_risk_level, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("another", "chat-1", 7, "/test", "Another", "[]", "low", "open", "now", "later"),
        )


def test_concurrent_asks_leave_only_one_open_interaction(tmp_path: Path) -> None:
    conversation = service(tmp_path)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def ask(prompt_summary: str) -> None:
        try:
            barrier.wait()
            conversation.ask(
                "chat-1",
                7,
                "/test",
                prompt_summary,
                [Choice(1, "Continue", "low", prompt_summary)],
            )
        except Exception as error:
            errors.append(error)

    first = threading.Thread(target=ask, args=("First prompt",))
    second = threading.Thread(target=ask, args=("Second prompt",))
    first.start()
    second.start()
    first.join()
    second.join()

    with conversation.database.connection() as connection:
        open_count = connection.execute(
            "SELECT COUNT(*) FROM conversation_pending_interactions WHERE chat_id = ? AND status = 'open'",
            ("chat-1",),
        ).fetchone()[0]

    assert errors == []
    assert open_count == 1


@pytest.mark.parametrize("label", ["oke", "OK", "Ya", "lanjut", "Setuju"])
def test_high_risk_choice_label_cannot_be_a_bare_contextual_word(tmp_path: Path, label: str) -> None:
    conversation = service(tmp_path)

    with pytest.raises(ConversationValidationError):
        conversation.ask(
            "chat-1",
            7,
            "/test",
            "Confirm a high-risk action",
            [Choice(1, label, "high", "dangerous-token")],
        )


def test_low_risk_choice_label_may_still_be_a_contextual_word(tmp_path: Path) -> None:
    conversation = service(tmp_path)

    conversation.ask(
        "chat-1",
        7,
        "/test",
        "Confirm a safe action",
        [Choice(1, "oke", "low", "safe-token")],
    )

    assert conversation.try_resolve_pending(7, "oke").action_token == "safe-token"


def test_interaction_content_is_screened_before_persistence(tmp_path: Path) -> None:
    conversation = service(tmp_path)

    with pytest.raises(ConversationValidationError):
        conversation.ask(
            "chat-1",
            7,
            "/test",
            "api_key=not-allowed",
            [Choice(1, "Continue", "low", "continue-token")],
        )
