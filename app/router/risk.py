"""Risk classifier and approval policy for the NOVA model router.

Sprint dependency
-----------------
Sprint 3 (Execution Orchestration) depends on this module (Sprint 3A risk
engine) being reviewed and accepted before activating any real adapter.  The
``assess_risk`` function is the single shared routing and risk-policy layer.
Do NOT create a duplicate policy classifier in ``app/execution/``.

Risk levels
-----------
LOW    -- informational, read-only, or purely generative tasks.
MEDIUM -- tasks that produce outputs intended for external use or that
          involve cross-workspace coordination.
HIGH   -- tasks that write, delete, send, publish, or take irreversible
          external actions.

Approval modes
--------------
NONE     -- proceed automatically (LOW risk, FAST workflow).
NOTIFY   -- proceed and surface a summary to the owner (MEDIUM risk).
REQUIRED -- stop and wait for explicit human confirmation (HIGH risk).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskAssessment:
    """Risk level and approval mode for a classified request."""

    risk_level: str      # LOW | MEDIUM | HIGH
    approval_mode: str   # NONE | NOTIFY | REQUIRED
    reason: str          # human-readable rationale


# ---------------------------------------------------------------------------
# Risk signals
# ---------------------------------------------------------------------------

_HIGH_RISK_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {"GOOGLE_WORKSPACE"}
)

_MEDIUM_RISK_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {"STRATEGY", "PRESENTATION", "ACADEMIC"}
)

_LOW_RISK_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {"GENERAL", "TECHNICAL", "FAST"}
)

# ---------------------------------------------------------------------------
# Destructive-command patterns — matched with word/token boundaries so that
# false positives like "commitment", "impostor", or explanatory text such as
# "do not run rm -rf" in a documentation sentence are not triggered.
#
# Each tuple is (label, compiled pattern).
# A match on ANY pattern escalates the request to HIGH risk.
# ---------------------------------------------------------------------------

_HIGH_RISK_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    # Shell destructive commands
    ("rm -rf",           re.compile(r"\brm\s+(?:-[^\s]*[rR][^\s]*[fF]|-rf|-fr|--no-preserve-root)\b", re.IGNORECASE)),
    ("git reset --hard", re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE)),
    ("git clean",        re.compile(r"\bgit\s+clean\b", re.IGNORECASE)),
    ("force push",       re.compile(r"\bgit\s+push\s+(?:--force|-f)\b|force\s+push\b", re.IGNORECASE)),
    ("drop table",       re.compile(r"\bdrop\s+table\b", re.IGNORECASE)),
    ("truncate table",   re.compile(r"\btruncate\s+(?:table\s+)?\w", re.IGNORECASE)),
    ("sudo",             re.compile(r"\bsudo\b", re.IGNORECASE)),
    ("chmod 777",        re.compile(r"\bchmod\s+777\b|\bchmod\s+a\+rwx\b", re.IGNORECASE)),
    ("shutdown",         re.compile(r"\bshutdown\b", re.IGNORECASE)),
    ("reboot",           re.compile(r"\breboot\b", re.IGNORECASE)),
    ("mkfs",             re.compile(r"\bmkfs\b", re.IGNORECASE)),
    ("dd if=",           re.compile(r"\bdd\s+if=", re.IGNORECASE)),
    ("curl|sh",          re.compile(r"\bcurl\b.{0,80}\|\s*(?:ba)?sh\b", re.IGNORECASE | re.DOTALL)),
    ("wget|sh",          re.compile(r"\bwget\b.{0,80}\|\s*(?:ba)?sh\b", re.IGNORECASE | re.DOTALL)),
    # High-risk action keywords (word-boundary anchored)
    ("send",             re.compile(r"\bsend\b", re.IGNORECASE)),
    ("kirim",            re.compile(r"\bkirim\b", re.IGNORECASE)),
    ("publish",          re.compile(r"\bpublish\b", re.IGNORECASE)),
    ("post",             re.compile(r"\bpost\b", re.IGNORECASE)),
    ("delete",           re.compile(r"\bdelete\b", re.IGNORECASE)),
    ("hapus",            re.compile(r"\bhapus\b", re.IGNORECASE)),
    ("remove",           re.compile(r"\bremove\b", re.IGNORECASE)),
    ("deploy",           re.compile(r"\bdeploy\b", re.IGNORECASE)),
    ("push",             re.compile(r"\bpush\b", re.IGNORECASE)),
    ("commit",           re.compile(r"\bcommit\b", re.IGNORECASE)),
    ("overwrite",        re.compile(r"\boverwrite\b", re.IGNORECASE)),
    ("update production",re.compile(r"\bupdate\s+production\b", re.IGNORECASE)),
    ("schedule",         re.compile(r"\bschedule\b", re.IGNORECASE)),
    ("jadwalkan",        re.compile(r"\bjadwalkan\b", re.IGNORECASE)),
    ("email",            re.compile(r"\bemail\b", re.IGNORECASE)),
    ("broadcast",        re.compile(r"\bbroadcast\b", re.IGNORECASE)),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assess_risk(workflow_id: str, message: str, confidence: str) -> RiskAssessment:
    """Determine risk level and approval mode for a classified request.

    Logic
    -----
    1. If the message matches a destructive-pattern regex → HIGH.
    2. If the workflow is inherently HIGH risk → HIGH.
    3. If the workflow is MEDIUM risk → MEDIUM.
    4. Otherwise → LOW.

    All patterns are compiled with word/token boundaries so that false
    positives such as "commitment", "impostor", or explanatory prose like
    "never run rm -rf in production" are handled correctly.
    """
    for label, pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(message):
            return RiskAssessment(
                risk_level="HIGH",
                approval_mode="REQUIRED",
                reason=f"Message matches high-risk pattern: '{label}'",
            )

    if workflow_id in _HIGH_RISK_WORKFLOWS:
        return RiskAssessment(
            risk_level="HIGH",
            approval_mode="REQUIRED",
            reason=f"Workflow '{workflow_id}' requires human approval before execution.",
        )

    if workflow_id in _MEDIUM_RISK_WORKFLOWS:
        return RiskAssessment(
            risk_level="MEDIUM",
            approval_mode="NOTIFY",
            reason=(
                f"Workflow '{workflow_id}' produces external-facing outputs; "
                "owner notification required."
            ),
        )

    if workflow_id == "FAST":
        return RiskAssessment(
            risk_level="LOW",
            approval_mode="NONE",
            reason="Fast triage workflow; no approval required.",
        )

    return RiskAssessment(
        risk_level="LOW",
        approval_mode="NONE",
        reason=f"Workflow '{workflow_id}' is informational; no approval required.",
    )


def approval_label(approval_mode: str) -> str:
    """Return a user-facing emoji + label for an approval mode."""
    return {
        "NONE": "✅ Auto",
        "NOTIFY": "🔔 Notify Owner",
        "REQUIRED": "🛑 Human Approval Required",
    }.get(approval_mode, approval_mode)
