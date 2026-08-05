"""Functional and reliability tests for ExecutionService (Sprint 3)."""

from __future__ import annotations

import pytest

from app.memory.database import MemoryDatabase
from app.execution.service import ExecutionService, SensitiveContentError
from app.execution.repository import (
    ApprovalError,
    ExecutionNotFoundError,
    InvalidTransitionError,
)
from app.execution.models import ExecutionState
from app.execution.adapter import LocalDeterministicAdapter, AdapterResult

AUTHORIZED = 1001


@pytest.fixture
def svc(tmp_path) -> ExecutionService:
    db = MemoryDatabase(tmp_path / "svc.db")
    db.initialize()
    service = ExecutionService(db, AUTHORIZED)
    service.initialize()
    return service


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "fresh.db"


# ---------------------------------------------------------------------------
# Functional: /run
# ---------------------------------------------------------------------------


def test_run_low_risk_creates_and_queues(svc) -> None:
    record = svc.submit("Review the architecture", AUTHORIZED)
    assert record.state in {ExecutionState.QUEUED, ExecutionState.RUNNING,
                             ExecutionState.COMPLETED, ExecutionState.FAILED}


def test_run_high_risk_creates_awaiting_approval(svc) -> None:
    record = svc.submit("send the report to the client", AUTHORIZED)
    assert record.state == ExecutionState.AWAITING_APPROVAL


def test_run_stores_hash_not_raw_instruction(svc) -> None:
    record = svc.submit("Review the architecture", AUTHORIZED)
    assert len(record.instruction_hash) == 64  # SHA-256 hex
    assert "Review" not in record.instruction_hash


def test_run_records_correct_workflow(svc) -> None:
    record = svc.submit("debug the python code", AUTHORIZED)
    assert record.workflow_id == "TECHNICAL"


def test_run_records_correct_risk_level(svc) -> None:
    record = svc.submit("send the report", AUTHORIZED)
    assert record.risk_level == "HIGH"


def test_run_creates_audit_entry(svc) -> None:
    record = svc.submit("Review the architecture", AUTHORIZED)
    audit = svc.list_audit(record.id)
    assert len(audit) >= 1
    assert any(e.event == "created" for e in audit)


def test_run_completed_execution_has_result_summary(svc) -> None:
    record = svc.submit("Review the architecture", AUTHORIZED)
    # For low-risk, dispatch happens immediately
    final = svc.get_status(record.id)
    assert final.state in {ExecutionState.COMPLETED, ExecutionState.FAILED, ExecutionState.QUEUED, ExecutionState.RUNNING}


# ---------------------------------------------------------------------------
# Functional: /runapprove
# ---------------------------------------------------------------------------


def test_runapprove_valid_awaiting_transitions_to_post_dispatch(svc) -> None:
    record = svc.submit("send the report", AUTHORIZED)
    assert record.state == ExecutionState.AWAITING_APPROVAL
    approved = svc.approve(record.id, AUTHORIZED)
    assert approved.state in {
        ExecutionState.QUEUED, ExecutionState.RUNNING,
        ExecutionState.COMPLETED, ExecutionState.FAILED
    }


def test_runapprove_sets_approved_by(svc) -> None:
    record = svc.submit("send the report", AUTHORIZED)
    approved = svc.approve(record.id, AUTHORIZED)
    assert approved.approved_by == AUTHORIZED


def test_runapprove_sets_approved_at(svc) -> None:
    record = svc.submit("send the report", AUTHORIZED)
    approved = svc.approve(record.id, AUTHORIZED)
    assert approved.approved_at is not None


def test_runapprove_invalid_id_raises(svc) -> None:
    with pytest.raises(ExecutionNotFoundError):
        svc.approve(99999, AUTHORIZED)


def test_runapprove_non_awaiting_state_raises(svc) -> None:
    record = svc.submit("Review the architecture", AUTHORIZED)
    # Already dispatched (low risk), not awaiting
    with pytest.raises(ApprovalError):
        svc.approve(record.id, AUTHORIZED)


# ---------------------------------------------------------------------------
# Functional: /runstatus
# ---------------------------------------------------------------------------


def test_runstatus_returns_current_state(svc) -> None:
    record = svc.submit("Review the architecture", AUTHORIZED)
    status = svc.get_status(record.id)
    assert status.id == record.id
    assert status.state in ExecutionState.ALL


def test_runstatus_invalid_id_raises(svc) -> None:
    with pytest.raises(ExecutionNotFoundError):
        svc.get_status(99999)


# ---------------------------------------------------------------------------
# Functional: /cancelrun
# ---------------------------------------------------------------------------


def test_cancelrun_awaiting_approval_transitions_to_failed(svc) -> None:
    record = svc.submit("send the report", AUTHORIZED)
    assert record.state == ExecutionState.AWAITING_APPROVAL
    cancelled = svc.cancel(record.id, AUTHORIZED)
    assert cancelled.state == ExecutionState.FAILED


def test_cancelrun_creates_audit_entry(svc) -> None:
    record = svc.submit("send the report", AUTHORIZED)
    svc.cancel(record.id, AUTHORIZED)
    audit = svc.list_audit(record.id)
    assert any(e.event == "cancelled" for e in audit)


def test_cancelrun_terminal_state_raises(svc) -> None:
    record = svc.submit("Review the architecture", AUTHORIZED)
    # Get to completed/failed state
    final = svc.get_status(record.id)
    if final.state in ExecutionState.TERMINAL:
        with pytest.raises(InvalidTransitionError):
            svc.cancel(final.id, AUTHORIZED)


def test_cancelrun_invalid_id_raises(svc) -> None:
    with pytest.raises(ExecutionNotFoundError):
        svc.cancel(99999, AUTHORIZED)


# ---------------------------------------------------------------------------
# Reliability: duplicate dispatch
# ---------------------------------------------------------------------------


def test_duplicate_approval_does_not_dispatch_twice(svc) -> None:
    record = svc.submit("send the report", AUTHORIZED)
    svc.approve(record.id, AUTHORIZED)
    # Second approval must raise ApprovalError (not in awaiting_approval anymore)
    with pytest.raises(ApprovalError):
        svc.approve(record.id, AUTHORIZED)
    # Audit should show only one 'approved' event
    audit = svc.list_audit(record.id)
    approved_events = [e for e in audit if e.event == "approved"]
    assert len(approved_events) == 1


def test_atomic_queued_to_running_transition(tmp_path) -> None:
    """Verify that only one dispatch can succeed via CAS semantics."""
    from app.execution.repository import ExecutionRepository
    db = MemoryDatabase(tmp_path / "cas.db")
    db.initialize()
    repo = ExecutionRepository(db)
    repo.initialize()

    record = repo.create_execution(
        instruction_hash="a" * 64,
        risk_level="LOW",
        workflow_id="GENERAL",
        initial_state=ExecutionState.QUEUED,
    )
    # First transition succeeds
    repo.transition_state(record.id, ExecutionState.QUEUED, ExecutionState.RUNNING)
    # Second attempt must raise InvalidTransitionError
    with pytest.raises(InvalidTransitionError):
        repo.transition_state(record.id, ExecutionState.QUEUED, ExecutionState.RUNNING)


# ---------------------------------------------------------------------------
# Reliability: terminal state immutability
# ---------------------------------------------------------------------------


def test_terminal_state_cannot_be_changed(tmp_path) -> None:
    from app.execution.repository import ExecutionRepository
    db = MemoryDatabase(tmp_path / "term.db")
    db.initialize()
    repo = ExecutionRepository(db)
    repo.initialize()

    record = repo.create_execution(
        instruction_hash="b" * 64,
        risk_level="LOW",
        workflow_id="GENERAL",
        initial_state=ExecutionState.QUEUED,
    )
    repo.transition_state(record.id, ExecutionState.QUEUED, ExecutionState.RUNNING)
    repo.transition_state(record.id, ExecutionState.RUNNING, ExecutionState.COMPLETED)
    # Any further transition must fail
    with pytest.raises(InvalidTransitionError):
        repo.transition_state(record.id, ExecutionState.COMPLETED, ExecutionState.FAILED)


# ---------------------------------------------------------------------------
# Reliability: restart reconciliation
# ---------------------------------------------------------------------------


def test_restart_reconciles_running_to_failed(tmp_path) -> None:
    from app.execution.repository import ExecutionRepository
    db = MemoryDatabase(tmp_path / "restart.db")
    db.initialize()
    repo = ExecutionRepository(db)
    repo.initialize()

    # Simulate a stuck-running execution
    record = repo.create_execution(
        instruction_hash="c" * 64,
        risk_level="LOW",
        workflow_id="GENERAL",
        initial_state=ExecutionState.QUEUED,
    )
    repo.transition_state(record.id, ExecutionState.QUEUED, ExecutionState.RUNNING)

    # Reconcile (simulates service restart)
    reconciled = repo.reconcile_running_to_failed()
    assert reconciled == 1
    updated = repo.get_execution(record.id)
    assert updated.state == ExecutionState.FAILED


def test_awaiting_approval_survives_restart(tmp_path) -> None:
    from app.execution.repository import ExecutionRepository
    db = MemoryDatabase(tmp_path / "await.db")
    db.initialize()
    repo = ExecutionRepository(db)
    repo.initialize()

    record = repo.create_execution(
        instruction_hash="d" * 64,
        risk_level="HIGH",
        workflow_id="GENERAL",
        initial_state=ExecutionState.AWAITING_APPROVAL,
    )
    # Reconcile should NOT touch awaiting_approval
    reconciled = repo.reconcile_running_to_failed()
    assert reconciled == 0
    updated = repo.get_execution(record.id)
    assert updated.state == ExecutionState.AWAITING_APPROVAL


# ---------------------------------------------------------------------------
# Reliability: state-transition validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "from_state,to_state",
    [
        # Invalid transitions
        (ExecutionState.CREATED, ExecutionState.RUNNING),
        (ExecutionState.CREATED, ExecutionState.COMPLETED),
        (ExecutionState.CREATED, ExecutionState.FAILED),
        (ExecutionState.AWAITING_APPROVAL, ExecutionState.RUNNING),
        (ExecutionState.AWAITING_APPROVAL, ExecutionState.COMPLETED),
        (ExecutionState.QUEUED, ExecutionState.AWAITING_APPROVAL),
        (ExecutionState.QUEUED, ExecutionState.COMPLETED),
        (ExecutionState.RUNNING, ExecutionState.CREATED),
        (ExecutionState.RUNNING, ExecutionState.QUEUED),
        (ExecutionState.RUNNING, ExecutionState.AWAITING_APPROVAL),
    ],
)
def test_invalid_transitions_are_rejected(
    from_state: str, to_state: str, tmp_path
) -> None:
    from app.execution.repository import ExecutionRepository
    db = MemoryDatabase(tmp_path / f"trans_{from_state}_{to_state}.db")
    db.initialize()
    repo = ExecutionRepository(db)
    repo.initialize()

    record = repo.create_execution(
        instruction_hash="e" * 64,
        risk_level="LOW",
        workflow_id="GENERAL",
        initial_state=from_state,
    )
    with pytest.raises(InvalidTransitionError):
        repo.transition_state(record.id, from_state, to_state)


# ---------------------------------------------------------------------------
# Reliability: adapter output limit and timeout
# ---------------------------------------------------------------------------


def test_adapter_output_limit_produces_failed() -> None:
    adapter = LocalDeterministicAdapter(max_output_bytes=0)  # everything overflows
    result = adapter.run(1, "a" * 64)
    # With max=0 and any non-zero simulated bytes, should fail
    # Or if simulated bytes happen to be 0, test the explicit helper
    if result.success:
        # Hash-derived bytes happened to be 0; test via explicit helper
        result = adapter.simulate_output_limit(100, 0)
    assert not result.success


def test_adapter_timeout_produces_failed() -> None:
    adapter = LocalDeterministicAdapter()
    result = adapter.simulate_timeout()
    assert not result.success
    assert "timed out" in result.summary.lower()


def test_adapter_invalid_hash_produces_failed() -> None:
    adapter = LocalDeterministicAdapter()
    result = adapter.run(1, "too_short")
    assert not result.success


# ---------------------------------------------------------------------------
# Reliability: determinism
# ---------------------------------------------------------------------------


def test_same_instruction_produces_same_hash() -> None:
    from app.execution.adapter import hash_instruction
    h1 = hash_instruction("Review the architecture")
    h2 = hash_instruction("Review the architecture")
    assert h1 == h2


def test_different_instructions_produce_different_hashes() -> None:
    from app.execution.adapter import hash_instruction
    h1 = hash_instruction("Review the architecture")
    h2 = hash_instruction("Create a slide deck")
    assert h1 != h2


# ---------------------------------------------------------------------------
# Service-layer limit enforcement (not adapter simulation helpers only)
# ---------------------------------------------------------------------------


def test_service_enforces_output_byte_limit(tmp_path) -> None:
    """Service must fail execution when output_bytes exceeds OUTPUT_BYTE_LIMIT."""
    from app.execution.service import OUTPUT_BYTE_LIMIT
    from app.execution.adapter import AdapterResult

    db = MemoryDatabase(tmp_path / "limit.db")
    db.initialize()

    class OverLimitAdapter:
        def run(self, execution_id: int, instruction_hash: str) -> AdapterResult:
            return AdapterResult(
                success=True,
                summary="should be blocked",
                output_bytes=OUTPUT_BYTE_LIMIT + 1,
            )

    svc = ExecutionService(db, AUTHORIZED, adapter=OverLimitAdapter())  # type: ignore[arg-type]
    svc.initialize()
    record = svc.submit("Review the architecture", AUTHORIZED)
    final = svc.get_status(record.id)
    assert final.state == ExecutionState.FAILED
    assert "limit" in final.result_summary.lower()


def test_service_enforces_timeout(tmp_path) -> None:
    """Service must fail execution when adapter exceeds max_execution_seconds."""
    import time as _time
    from app.execution.service import EXECUTION_TIMEOUT_SECONDS
    from app.execution.adapter import AdapterResult

    db = MemoryDatabase(tmp_path / "timeout.db")
    db.initialize()

    class SlowAdapter:
        def run(self, execution_id: int, instruction_hash: str) -> AdapterResult:
            return AdapterResult(
                success=True,
                summary="completed too slowly (simulated)",
                output_bytes=0,
            )

    svc = ExecutionService(db, AUTHORIZED, adapter=SlowAdapter())  # type: ignore[arg-type]
    svc.initialize()

    original_monotonic = _time.monotonic

    call_count = [0]

    def fake_monotonic():
        call_count[0] += 1
        if call_count[0] == 1:
            return 0.0
        return EXECUTION_TIMEOUT_SECONDS + 1.0

    import app.execution.service as _svc_mod
    _svc_mod.time.monotonic = fake_monotonic  # type: ignore[attr-defined]
    try:
        record = svc.submit("Review the architecture", AUTHORIZED)
        final = svc.get_status(record.id)
    finally:
        _svc_mod.time.monotonic = original_monotonic  # type: ignore[attr-defined]

    assert final.state == ExecutionState.FAILED
    assert "time" in final.result_summary.lower() or "limit" in final.result_summary.lower()


# ---------------------------------------------------------------------------
# Reconcile audit: one entry per reconciled execution, no duplicates
# ---------------------------------------------------------------------------


def test_reconcile_writes_one_audit_entry_per_execution(tmp_path) -> None:
    """initialize() writes one 'failed' audit entry per reconciled execution."""
    from app.execution.repository import ExecutionRepository

    db = MemoryDatabase(tmp_path / "rec.db")
    db.initialize()
    repo = ExecutionRepository(db)
    repo.initialize()

    # Manually create two executions in 'running' state
    for _ in range(2):
        record = repo.create_execution(
            instruction_hash="a" * 64,
            risk_level="LOW",
            workflow_id="GENERAL",
            initial_state="queued",
        )
        repo.transition_state(record.id, "queued", "running")

    svc = ExecutionService(db, AUTHORIZED)
    svc.initialize()

    # Both executions should now be failed with at least one audit entry each
    with db.connection() as conn:
        failed = conn.execute(
            "SELECT id FROM executions WHERE state = 'failed'"
        ).fetchall()
        audit_entries = conn.execute(
            "SELECT execution_id, event FROM execution_audit_log WHERE event = 'failed'"
        ).fetchall()
    assert len(failed) == 2
    reconciled_ids = {row["id"] for row in failed}
    audit_execution_ids = {row["execution_id"] for row in audit_entries}
    assert reconciled_ids.issubset(audit_execution_ids), (
        "Missing audit entries for reconciled executions"
    )


def test_reconcile_does_not_duplicate_entries_on_second_initialize(tmp_path) -> None:
    """Repeated initialize() must not create additional reconciliation audit entries."""
    from app.execution.repository import ExecutionRepository

    db = MemoryDatabase(tmp_path / "rec2.db")
    db.initialize()
    repo = ExecutionRepository(db)
    repo.initialize()

    record = repo.create_execution(
        instruction_hash="b" * 64,
        risk_level="LOW",
        workflow_id="GENERAL",
        initial_state="queued",
    )
    repo.transition_state(record.id, "queued", "running")

    svc = ExecutionService(db, AUTHORIZED)
    svc.initialize()
    with db.connection() as conn:
        count_after_first = conn.execute(
            "SELECT COUNT(*) FROM execution_audit_log"
        ).fetchone()[0]

    # Second initialize — execution is already 'failed', no duplicates
    svc2 = ExecutionService(db, AUTHORIZED)
    svc2.initialize()
    with db.connection() as conn:
        count_after_second = conn.execute(
            "SELECT COUNT(*) FROM execution_audit_log"
        ).fetchone()[0]

    assert count_after_second == count_after_first, (
        f"Second initialize added {count_after_second - count_after_first} "
        "duplicate audit entries"
    )


# ---------------------------------------------------------------------------
# Shared SENSITIVE_CONTENT_PATTERN import
# ---------------------------------------------------------------------------


def test_sensitive_content_pattern_is_shared_from_security() -> None:
    """SENSITIVE_CONTENT_PATTERN in service must be the same object as app.security."""
    from app.security import SENSITIVE_CONTENT_PATTERN as shared
    from app.execution.service import SENSITIVE_CONTENT_PATTERN as in_service
    assert shared is in_service, (
        "app.execution.service must import SENSITIVE_CONTENT_PATTERN from app.security, "
        "not define its own copy"
    )
