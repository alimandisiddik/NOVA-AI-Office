"""Telegram message formatters for the execution subsystem.

No SQL, no business logic, no provider calls.
Input is always an ExecutionRecord or AuditEntry list.
"""

from __future__ import annotations

from app.execution.models import AuditEntry, ExecutionRecord, ExecutionState

_STATE_EMOJI: dict[str, str] = {
    ExecutionState.CREATED: "🆕",
    ExecutionState.AWAITING_APPROVAL: "⏳",
    ExecutionState.QUEUED: "🔄",
    ExecutionState.RUNNING: "⚙️",
    ExecutionState.COMPLETED: "✅",
    ExecutionState.FAILED: "❌",
}


def _state_emoji(state: str) -> str:
    return _STATE_EMOJI.get(state, "❓")


def execution_created_message(record: ExecutionRecord) -> str:
    """Render the confirmation message after /run."""
    emoji = _state_emoji(record.state)
    lines = [
        f"{emoji} Execution #{record.id} created",
        "",
        f"Workflow:   {record.workflow_id}",
        f"Risk:       {record.risk_level}",
        f"State:      {record.state}",
    ]
    if record.state == ExecutionState.AWAITING_APPROVAL:
        lines.append("")
        lines.append(
            f"🛑 This execution requires approval. "
            f"Use /runapprove {record.id} to approve "
            f"or /cancelrun {record.id} to reject."
        )
    elif record.state == ExecutionState.COMPLETED:
        lines.append("")
        lines.append(f"Result: {record.result_summary}")
    elif record.state == ExecutionState.FAILED:
        lines.append("")
        lines.append(f"Failure: {record.result_summary}")
    return "\n".join(lines)


def execution_status_message(record: ExecutionRecord) -> str:
    """Render the status message for /runstatus."""
    emoji = _state_emoji(record.state)
    lines = [
        f"{emoji} Execution #{record.id}",
        "",
        f"Workflow:    {record.workflow_id}",
        f"Risk:        {record.risk_level}",
        f"State:       {record.state}",
        f"Created:     {record.created_at}",
        f"Updated:     {record.updated_at}",
    ]
    if record.approved_by is not None:
        lines.append(f"Approved by: {record.approved_by}")
        lines.append(f"Approved at: {record.approved_at}")
    if record.result_summary:
        lines.append("")
        lines.append(f"Result: {record.result_summary}")
    return "\n".join(lines)


def execution_approved_message(record: ExecutionRecord) -> str:
    """Render the approval confirmation message for /runapprove."""
    emoji = _state_emoji(record.state)
    lines = [
        f"✅ Execution #{record.id} approved",
        "",
        f"State:  {emoji} {record.state}",
    ]
    if record.result_summary:
        lines.append("")
        lines.append(f"Result: {record.result_summary}")
    return "\n".join(lines)


def execution_cancelled_message(record: ExecutionRecord) -> str:
    """Render the cancellation confirmation message for /cancelrun."""
    return (
        f"🚫 Execution #{record.id} cancelled\n"
        f"\n"
        f"State: {_state_emoji(record.state)} {record.state}\n"
        f"Reason: {record.result_summary}"
    )
