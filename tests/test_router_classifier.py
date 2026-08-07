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


# ---------------------------------------------------------------------------
# Sprint 5G.2 — informational vs. execution intent regression cases
#
# Root cause: "fungsi" (and other technical domain nouns) was a first-match
# TECHNICAL trigger regardless of intent, so a purely explanatory question
# containing a technical noun was misrouted into the TECHNICAL workflow.
# These cases lock in the fix: explicit technical ACTION verbs still route
# to TECHNICAL unconditionally; technical/strategy DOMAIN nouns only route
# to their workflow when the sentence is not itself an explanatory/
# informational question about that noun. See docs/SPRINT_5G2.md.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected_workflow",
    [
        # --- Required acceptance cases (docs/SPRINT_5G2.md) -----------------
        # 1. The confirmed runtime defect reproduction.
        (
            "Jelaskan dalam 3 kalimat apa fungsi Executive Control Tower di NOVA.",
            "GENERAL",
        ),
        # 2. Informational technical noun ("fungsi") + explanatory verb.
        ("Jelaskan fungsi database.", "GENERAL"),
        # 3. Informational technical noun via Indonesian question pattern.
        ("Apa fungsi NOVA Router?", "GENERAL"),
        # 4. Explicit technical action verb ("perbaiki") must win.
        ("Perbaiki fungsi database connection ini.", "TECHNICAL"),
        # 5. Explicit technical action verb ("debug") must win.
        ("Debug fungsi generate_text.", "TECHNICAL"),
        # 6. Domain noun alone, no explanatory phrasing → TECHNICAL preserved.
        ("Review architecture NOVA.", "TECHNICAL"),
        # 7. Explicit action verb ("implement") must win regardless of nouns.
        ("Implement provider adapter baru.", "TECHNICAL"),
        # 8. Explicit action verb ("refactor") must win.
        ("Refactor class provider gateway.", "TECHNICAL"),
        # 9. Strategy noun, no explanatory phrasing → STRATEGY preserved.
        ("Buat strategi NOVA 2027.", "STRATEGY"),
        # 10. Strategy noun + explanatory phrasing → GENERAL (documented
        #     deterministic policy: same guard as the technical-domain fix).
        ("Jelaskan strategi NOVA 2027.", "GENERAL"),
        # 11. Concrete Google Workspace operation must not be affected.
        ("Cari dokumen di Google Drive.", "GOOGLE_WORKSPACE"),
        # 12. Concrete presentation asset must not be affected.
        ("Buat slide executive summary.", "PRESENTATION"),
        # 13. "review" must not hijack academic routing.
        ("Review literatur untuk disertasi.", "ACADEMIC"),
        # 14. Frozen single-word precedence: FAST wins before TECHNICAL.
        ("debug", "FAST"),
        # --- Additional English-phrasing regression coverage ----------------
        ("Explain what the database module does.", "GENERAL"),
        ("What is an API?", "GENERAL"),
        ("What does the endpoint schema do?", "GENERAL"),
        ("Describe the server architecture.", "GENERAL"),
        # --- Guard: informational GWS/Presentation/Academic remain intact ---
        ("Jelaskan cara mengirim email lewat Gmail", "GOOGLE_WORKSPACE"),
        ("Jelaskan slide ini", "PRESENTATION"),
        ("Jelaskan metodologi penelitian", "ACADEMIC"),
    ],
)
def test_classify_intent_intent_vs_domain_regression(
    message: str, expected_workflow: str
) -> None:
    result = classify_intent(message)
    assert result.workflow_id == expected_workflow, (
        f"'{message}' → expected {expected_workflow}, got {result.workflow_id} "
        f"(rule: {result.matched_rule})"
    )


def test_technical_action_verb_overrides_explanatory_phrasing() -> None:
    """An explicit action verb wins even inside an explanatory sentence."""
    result = classify_intent("Jelaskan cara debug fungsi generate_text.")
    assert result.workflow_id == "TECHNICAL"
    assert result.matched_rule.startswith("technical_action:")


def test_informational_technical_domain_has_medium_confidence() -> None:
    """Suppressed weak domain matches still carry a traceable MEDIUM
    confidence, distinct from the plain LOW-confidence default fallback."""
    result = classify_intent("Jelaskan fungsi database.")
    assert result.workflow_id == "GENERAL"
    assert result.confidence == "MEDIUM"
    assert result.matched_rule.startswith("informational_override:")


def test_plain_fallback_still_has_low_confidence() -> None:
    """Messages with no technical/strategy signal at all are unaffected —
    they keep the original LOW-confidence default fallback."""
    result = classify_intent("Hello NOVA, how are you today?")
    assert result.workflow_id == "GENERAL"
    assert result.confidence == "LOW"
    assert result.matched_rule == "default_fallback"
