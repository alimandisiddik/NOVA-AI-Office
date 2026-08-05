"""Tests for the deterministic intent classifier (app/router/classifier.py)."""

import pytest

from app.router.classifier import classify_intent

# ---------------------------------------------------------------------------
# Workflow routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected_workflow",
    [
        # TECHNICAL
        ("Please debug this Python function", "TECHNICAL"),
        ("Refactor the code for the API endpoint", "TECHNICAL"),
        ("Write a pytest for the database module", "TECHNICAL"),
        ("Review the architecture of the backend", "TECHNICAL"),
        # GOOGLE_WORKSPACE
        ("Upload the file to Google Drive", "GOOGLE_WORKSPACE"),
        ("Send an email via Gmail to the team", "GOOGLE_WORKSPACE"),
        ("Add event to Google Calendar", "GOOGLE_WORKSPACE"),
        # ACADEMIC
        ("Synthesise the research literature on AI ethics", "ACADEMIC"),
        ("Write an abstract for my thesis on NLP", "ACADEMIC"),
        ("Find references for the jurnal paper", "ACADEMIC"),
        # PRESENTATION
        ("Create a slide deck for the quarterly review", "PRESENTATION"),
        ("Write speaker notes for the pitchdeck", "PRESENTATION"),
        # STRATEGY
        ("Define OKR goals for Q3 and roadmap priorities", "STRATEGY"),
        ("What is our vision and mission statement?", "STRATEGY"),
        # FAST
        ("yes", "FAST"),
        ("no", "FAST"),
        ("ping", "FAST"),
        ("ok?", "FAST"),
        # GENERAL (fallback)
        ("Hello NOVA", "GENERAL"),
        ("What should I do next?", "GENERAL"),
    ],
)
def test_classify_intent_routes_to_expected_workflow(
    message: str, expected_workflow: str
) -> None:
    result = classify_intent(message)
    assert result.workflow_id == expected_workflow, (
        f"'{message}' → expected {expected_workflow}, got {result.workflow_id} "
        f"(rule: {result.matched_rule})"
    )


# ---------------------------------------------------------------------------
# Confidence levels
# ---------------------------------------------------------------------------


def test_technical_with_many_keywords_has_high_confidence() -> None:
    result = classify_intent("debug the python code and fix the error in the function")
    assert result.confidence == "HIGH"
    assert result.workflow_id == "TECHNICAL"


def test_single_technical_keyword_may_be_medium_confidence() -> None:
    result = classify_intent("what is code?")
    assert result.confidence in {"HIGH", "MEDIUM"}


def test_fallback_has_low_confidence() -> None:
    result = classify_intent("Hello there NOVA")
    assert result.workflow_id == "GENERAL"
    assert result.confidence == "LOW"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_message_routes_to_fast() -> None:
    result = classify_intent("")
    assert result.workflow_id == "FAST"


def test_whitespace_only_routes_to_fast() -> None:
    result = classify_intent("   ")
    assert result.workflow_id == "FAST"


def test_result_is_frozen_dataclass() -> None:
    result = classify_intent("test message")
    with pytest.raises((AttributeError, TypeError)):
        result.workflow_id = "CHANGED"  # type: ignore[misc]


def test_matched_rule_is_non_empty_string() -> None:
    result = classify_intent("debug the code")
    assert isinstance(result.matched_rule, str)
    assert len(result.matched_rule) > 0
