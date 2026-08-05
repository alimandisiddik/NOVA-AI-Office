"""Tests for execution formatters (Sprint 3)."""

from __future__ import annotations

import pytest

from app.execution.models import ExecutionRecord, ExecutionState
from app.execution.formatters import (
    execution_created_message,
    execution_status_message,
    execution_approved_message,
    execution_cancelled_message,
)


def _record(
    state: str = ExecutionState.COMPLETED,
    risk_level: str = "LOW",
    approved_by: int | None = None,
    approved_at: str | None = None,
    result_summary: str = "Done",
) -> ExecutionRecord:
    return ExecutionRecord(
        id=42,
        instruction_hash="a" * 64,
        risk_level=risk_level,
        workflow_id="TECHNICAL",
        state=state,
        approved_by=approved_by,
        approved_at=approved_at,
        result_summary=result_summary,
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:01:00Z",
    )


def test_created_message_contains_execution_id() -> None:
    msg = execution_created_message(_record(state=ExecutionState.COMPLETED))
    assert "42" in msg


def test_created_message_contains_state() -> None:
    msg = execution_created_message(_record(state=ExecutionState.AWAITING_APPROVAL))
    assert ExecutionState.AWAITING_APPROVAL in msg


def test_created_message_awaiting_includes_approve_hint() -> None:
    msg = execution_created_message(_record(state=ExecutionState.AWAITING_APPROVAL, risk_level="HIGH"))
    assert "runapprove" in msg
    assert "cancelrun" in msg


def test_created_message_completed_includes_result() -> None:
    msg = execution_created_message(_record(state=ExecutionState.COMPLETED, result_summary="All done"))
    assert "All done" in msg


def test_status_message_contains_workflow() -> None:
    msg = execution_status_message(_record())
    assert "TECHNICAL" in msg


def test_status_message_contains_risk() -> None:
    msg = execution_status_message(_record(risk_level="HIGH"))
    assert "HIGH" in msg


def test_status_message_shows_approval_info_when_present() -> None:
    msg = execution_status_message(
        _record(approved_by=1001, approved_at="2026-08-06T00:00:30Z")
    )
    assert "1001" in msg
    assert "2026-08-06" in msg


def test_approved_message_contains_execution_id() -> None:
    msg = execution_approved_message(_record(state=ExecutionState.COMPLETED))
    assert "42" in msg


def test_cancelled_message_contains_execution_id() -> None:
    msg = execution_cancelled_message(_record(state=ExecutionState.FAILED, result_summary="Cancelled by user:1001"))
    assert "42" in msg
    assert ExecutionState.FAILED in msg


def test_no_raw_instruction_in_any_formatter() -> None:
    raw = "api_key=supersecret_value"
    record = _record(result_summary="completed")
    for fn in (
        execution_created_message,
        execution_status_message,
        execution_approved_message,
        execution_cancelled_message,
    ):
        assert raw not in fn(record), f"{fn.__name__} leaked raw instruction"
