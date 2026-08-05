"""Immutable domain models for NOVA execution orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# ---------------------------------------------------------------------------
# Canonical execution states
# ---------------------------------------------------------------------------

class ExecutionState:
    """Namespace for the six canonical execution state strings."""

    CREATED: Final = "created"
    AWAITING_APPROVAL: Final = "awaiting_approval"
    QUEUED: Final = "queued"
    RUNNING: Final = "running"
    COMPLETED: Final = "completed"
    FAILED: Final = "failed"

    ALL: Final = frozenset(
        {CREATED, AWAITING_APPROVAL, QUEUED, RUNNING, COMPLETED, FAILED}
    )
    TERMINAL: Final = frozenset({COMPLETED, FAILED})
    NON_TERMINAL: Final = frozenset({CREATED, AWAITING_APPROVAL, QUEUED, RUNNING})

    # Allowed transitions: source → set of allowed targets
    TRANSITIONS: Final[dict[str, frozenset[str]]] = {
        CREATED: frozenset({AWAITING_APPROVAL, QUEUED}),
        AWAITING_APPROVAL: frozenset({QUEUED, FAILED}),
        QUEUED: frozenset({RUNNING, FAILED}),
        RUNNING: frozenset({COMPLETED, FAILED}),
        COMPLETED: frozenset(),
        FAILED: frozenset(),
    }


# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionRecord:
    """An immutable snapshot of a single execution row."""

    id: int
    instruction_hash: str        # SHA-256 hex of the raw instruction — never the raw value
    risk_level: str              # LOW | MEDIUM | HIGH
    workflow_id: str
    state: str
    approved_by: int | None      # Telegram user_id of approver, or None
    approved_at: str | None      # UTC ISO-8601, or None
    result_summary: str          # brief outcome text; never raw instruction
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AuditEntry:
    """An immutable snapshot of a single audit-log row."""

    id: int
    execution_id: int
    event: str
    actor: str                   # "system" | "user:<telegram_user_id>"
    detail: str                  # safe, non-sensitive context
    created_at: str
