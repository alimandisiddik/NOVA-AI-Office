"""Execution-plan generator for the NOVA model router.

generate_plan() is the single entry point.  It orchestrates:
  1. intent classification  (classifier.py)
  2. workflow lookup        (workflows.py)
  3. role resolution        (roles.py)
  4. risk assessment        (risk.py)
  5. plan assembly          (this file)

No provider SDK is imported and no network call is made.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.router.classifier import ClassificationResult, classify_intent
from app.router.risk import RiskAssessment, approval_label, assess_risk
from app.router.roles import Role, get_role
from app.router.workflows import Workflow, get_workflow


# ---------------------------------------------------------------------------
# Data type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionPlan:
    """A fully resolved, provider-agnostic execution plan."""

    message: str
    classification: ClassificationResult
    workflow: Workflow
    primary_roles: tuple[Role, ...]
    support_roles: tuple[Role, ...]
    risk: RiskAssessment
    notes: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_plan(message: str) -> ExecutionPlan:
    """Classify a message and build a complete execution plan.

    All roles are resolved from the registry so that any future provider
    adapter can be substituted transparently.

    Parameters
    ----------
    message:
        The raw user message (plain text).  Must not be empty.

    Returns
    -------
    ExecutionPlan
        A fully populated, immutable plan.  Provider status will always
        be NOT_CONNECTED until real adapters are registered.
    """
    if not message or not message.strip():
        message = "(empty)"

    classification = classify_intent(message)
    workflow = get_workflow(classification.workflow_id)
    risk = assess_risk(classification.workflow_id, message, classification.confidence)

    primary_roles = tuple(get_role(rid) for rid in workflow.primary_roles)
    support_roles = tuple(get_role(rid) for rid in workflow.support_roles)

    notes: list[str] = []
    if risk.approval_mode == "REQUIRED":
        notes.append(
            "⚠️  This plan requires explicit human approval before any action is taken."
        )
    if risk.approval_mode == "NOTIFY":
        notes.append(
            "ℹ️  Owner will be notified when this plan produces external-facing output."
        )
    for role in (*primary_roles, *support_roles):
        if role.connection_status == "NOT_CONNECTED":
            notes.append(
                f"🔌  {role.display_name} ({role.provider_label}) is NOT_CONNECTED — "
                "execution will be simulated until a real adapter is registered."
            )
            break  # one notice is enough

    return ExecutionPlan(
        message=message,
        classification=classification,
        workflow=workflow,
        primary_roles=primary_roles,
        support_roles=support_roles,
        risk=risk,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_plan(plan: ExecutionPlan) -> str:
    """Render an ExecutionPlan as a human-readable Telegram message."""
    primary_role_lines = "\n".join(
        f"  • {role.display_name} [{role.provider_label}] — {role.connection_status}"
        for role in plan.primary_roles
    )
    support_role_lines = (
        "\n".join(
            f"  • {role.display_name} [{role.provider_label}]"
            for role in plan.support_roles
        )
        if plan.support_roles
        else "  (none)"
    )

    lines = [
        "📋 NOVA Execution Plan",
        "",
        f"Workflow:    {plan.workflow.display_name} ({plan.workflow.workflow_id})",
        f"Confidence:  {plan.classification.confidence}",
        f"Rule:        {plan.classification.matched_rule}",
        "",
        "Primary Roles:",
        primary_role_lines,
        "",
        "Support Roles:",
        support_role_lines,
        "",
        f"Risk Level:  {plan.risk.risk_level}",
        f"Approval:    {approval_label(plan.risk.approval_mode)}",
        f"Reason:      {plan.risk.reason}",
    ]

    if plan.notes:
        lines.append("")
        lines.extend(plan.notes)

    return "\n".join(lines)


def format_route(plan: ExecutionPlan) -> str:
    """Render a compact routing summary for the /route command."""
    primary = ", ".join(
        f"{role.display_name} [{role.provider_label}]"
        for role in plan.primary_roles
    )
    return (
        f"🔀 Route: {plan.workflow.display_name} | "
        f"{plan.classification.confidence} confidence\n"
        f"Roles: {primary}\n"
        f"Risk: {plan.risk.risk_level} | "
        f"Approval: {approval_label(plan.risk.approval_mode)}"
    )


def format_router_status(roles: list[Role], workflows: list[Workflow]) -> str:
    """Render the /router_status overview."""
    role_lines = "\n".join(
        f"  {role.role_id:<22} {role.provider_label:<34} {role.connection_status}"
        for role in roles
    )
    workflow_lines = "\n".join(
        f"  {wf.workflow_id:<18} {', '.join(wf.primary_roles)}"
        for wf in workflows
    )
    return (
        "🤖 NOVA Router Status\n"
        "\n"
        "Roles:\n"
        f"{role_lines}\n"
        "\n"
        "Workflows:\n"
        f"{workflow_lines}\n"
        "\n"
        "Provider connections: NOT_CONNECTED (simulation mode)"
    )
