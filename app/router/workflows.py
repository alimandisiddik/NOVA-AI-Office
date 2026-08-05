"""Workflow registry for the NOVA model router.

Each workflow defines the ordered roles that participate in its execution
and the human-readable description of when it applies.  No provider call
is made here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final


@dataclass(frozen=True)
class Workflow:
    """A named workflow with its participant roles."""

    workflow_id: str
    display_name: str
    description: str
    primary_roles: tuple[str, ...]
    support_roles: tuple[str, ...] = field(default_factory=tuple)


_REGISTRY: Final[dict[str, Workflow]] = {
    "GENERAL": Workflow(
        workflow_id="GENERAL",
        display_name="General",
        description="Default workflow for everyday questions and requests that do not "
        "match a more specific category.",
        primary_roles=("CONTROL_TOWER",),
        support_roles=("FAST_ROUTER",),
    ),
    "STRATEGY": Workflow(
        workflow_id="STRATEGY",
        display_name="Strategy",
        description="Strategic planning, goal setting, prioritisation, and executive "
        "decision-making across workspaces.",
        primary_roles=("CONTROL_TOWER",),
        support_roles=("WORKSPACE_KNOWLEDGE",),
    ),
    "GOOGLE_WORKSPACE": Workflow(
        workflow_id="GOOGLE_WORKSPACE",
        display_name="Google Workspace",
        description="Tasks involving Google Drive, Docs, Sheets, Gmail, or Calendar "
        "within an approved service boundary.",
        primary_roles=("WORKSPACE_KNOWLEDGE",),
        support_roles=("EXECUTION_WORKER",),
    ),
    "TECHNICAL": Workflow(
        workflow_id="TECHNICAL",
        display_name="Technical",
        description="Software design, code generation, architecture reviews, "
        "debugging, and engineering specifications.",
        primary_roles=("TECHNICAL_ARCHITECT", "EXECUTION_WORKER"),
        support_roles=("CONTROL_TOWER",),
    ),
    "PRESENTATION": Workflow(
        workflow_id="PRESENTATION",
        display_name="Presentation",
        description="Slide decks, visual briefs, speaker notes, and structured "
        "presentation assets.",
        primary_roles=("CONTROL_TOWER", "WORKSPACE_KNOWLEDGE"),
        support_roles=(),
    ),
    "ACADEMIC": Workflow(
        workflow_id="ACADEMIC",
        display_name="Academic",
        description="Research synthesis, literature review, citation management, "
        "and academic writing support.",
        primary_roles=("WORKSPACE_KNOWLEDGE",),
        support_roles=("CONTROL_TOWER",),
    ),
    "FAST": Workflow(
        workflow_id="FAST",
        display_name="Fast",
        description="Low-latency triage: short lookups, simple yes/no questions, "
        "and quick classification tasks.",
        primary_roles=("FAST_ROUTER",),
        support_roles=(),
    ),
}


class WorkflowNotFoundError(KeyError):
    """Raised when a requested workflow ID is not in the registry."""


def get_workflow(workflow_id: str) -> Workflow:
    """Return the Workflow for the given workflow_id.

    Raises WorkflowNotFoundError if the workflow is not registered.
    """
    try:
        return _REGISTRY[workflow_id.upper()]
    except KeyError as exc:
        raise WorkflowNotFoundError(f"Unknown workflow: {workflow_id!r}") from exc


def list_workflows() -> list[Workflow]:
    """Return all registered workflows in canonical order."""
    return list(_REGISTRY.values())
