"""Logical role registry for the NOVA model router.

Roles are logical abstractions.  No provider SDK is imported and no
network call is made.  Each role maps to a provider label so that future
adapters can be dropped in behind a service boundary without touching
router logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Role:
    """A logical router role with its provider assignment and responsibility."""

    role_id: str
    display_name: str
    provider_label: str
    responsibility: str
    connection_status: str = "NOT_CONNECTED"


_REGISTRY: Final[dict[str, Role]] = {
    "CONTROL_TOWER": Role(
        role_id="CONTROL_TOWER",
        display_name="Control Tower",
        provider_label="ChatGPT",
        responsibility=(
            "Orchestrates multi-step plans, coordinates handoffs between roles, "
            "and governs executive-level decisions."
        ),
    ),
    "WORKSPACE_KNOWLEDGE": Role(
        role_id="WORKSPACE_KNOWLEDGE",
        display_name="Workspace Knowledge",
        provider_label="Gemini",
        responsibility=(
            "Retrieves, synthesises, and manages knowledge assets across "
            "approved workspace sources."
        ),
    ),
    "TECHNICAL_ARCHITECT": Role(
        role_id="TECHNICAL_ARCHITECT",
        display_name="Technical Architect",
        provider_label="Claude",
        responsibility=(
            "Designs system architecture, reviews code, evaluates technical "
            "trade-offs, and produces engineering specifications."
        ),
    ),
    "EXECUTION_WORKER": Role(
        role_id="EXECUTION_WORKER",
        display_name="Execution Worker",
        provider_label="Codex",
        responsibility=(
            "Implements deterministic tasks: writing code, running scripts, "
            "applying patches, and producing structured file outputs."
        ),
    ),
    "FAST_ROUTER": Role(
        role_id="FAST_ROUTER",
        display_name="Fast Router",
        provider_label="Configurable lightweight model",
        responsibility=(
            "Handles low-latency classification, triage, and simple retrieval "
            "tasks that do not require deep reasoning."
        ),
    ),
}


class RoleNotFoundError(KeyError):
    """Raised when a requested role ID is not in the registry."""


def get_role(role_id: str) -> Role:
    """Return the Role for the given role_id.

    Raises RoleNotFoundError if the role is not registered.
    """
    try:
        return _REGISTRY[role_id.upper()]
    except KeyError as exc:
        raise RoleNotFoundError(f"Unknown role: {role_id!r}") from exc


def list_roles() -> list[Role]:
    """Return all registered roles in canonical order."""
    return list(_REGISTRY.values())
