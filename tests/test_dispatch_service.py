import pytest
from app.memory.database import MemoryDatabase
from app.dispatch.service import DispatchService
from app.dispatch.approvals import ApprovalService
from app.dispatch.registry import AgentRegistry
from app.dispatch.models import DispatchRequest
from app.dispatch.errors import ApprovalRequiredError, InvalidTransitionError, DuplicateDispatchError, CancellationError, RetryExhaustedError, InvalidRequestError

@pytest.fixture
def memory_db(tmp_path):
    db = MemoryDatabase(tmp_path / "nova.sqlite3")
    return db

@pytest.fixture
def approval_svc(memory_db):
    svc = ApprovalService(memory_db, authorized_user_id=123)
    return svc

@pytest.fixture
def dispatch_svc(memory_db, approval_svc):
    svc = DispatchService(memory_db, registry=AgentRegistry(), approvals=approval_svc)
    svc.initialize()
    return svc

def test_initialize_is_idempotent(dispatch_svc):
    dispatch_svc.initialize() # Should not raise

def test_create_dispatch_approval_free(dispatch_svc):
    req = DispatchRequest("telegram_direct", "msg:1", "document_agent", "read_only", "ref", "key1", "user:123")
    record = dispatch_svc.create_dispatch(req, "user:123")
    assert record.status == "pending"

def test_create_dispatch_approval_required(dispatch_svc):
    req = DispatchRequest("telegram_direct", "msg:2", "procurement_agent", "external_communication", "ref", "key2", "user:123")
    record = dispatch_svc.create_dispatch(req, "user:123")
    assert record.status == "awaiting_approval"

def test_create_dispatch_idempotent(dispatch_svc):
    req = DispatchRequest("telegram_direct", "msg:3", "document_agent", "read_only", "ref", "key3", "user:123")
    record1 = dispatch_svc.create_dispatch(req, "user:123")
    record2 = dispatch_svc.create_dispatch(req, "user:123")
    assert record1.dispatch_id == record2.dispatch_id

def test_dispatch_fails_if_awaiting_approval(dispatch_svc):
    req = DispatchRequest("telegram_direct", "msg:4", "procurement_agent", "external_communication", "ref", "key4", "user:123")
    record = dispatch_svc.create_dispatch(req, "user:123")
    with pytest.raises(ApprovalRequiredError):
        dispatch_svc.dispatch(record.dispatch_id)

def test_dispatch_success_flow(dispatch_svc):
    req = DispatchRequest("telegram_direct", "msg:5", "document_agent", "read_only", "ref", "key5", "user:123")
    record = dispatch_svc.create_dispatch(req, "user:123")
    finished_record = dispatch_svc.dispatch(record.dispatch_id)
    assert finished_record.status == "succeeded"

def test_cancel_dispatch(dispatch_svc):
    req = DispatchRequest("telegram_direct", "msg:6", "document_agent", "read_only", "ref", "key6", "user:123")
    record = dispatch_svc.create_dispatch(req, "user:123")
    res = dispatch_svc.cancel_dispatch(record.dispatch_id, "user:123", "cancel test")
    assert res.status == "cancelled"

    # Cannot cancel from terminal state
    with pytest.raises(CancellationError):
        dispatch_svc.cancel_dispatch(record.dispatch_id, "user:123", "cancel test 2")

def test_retry_dispatch_exhaustion(dispatch_svc):
    # Mocking failure is easier via DB directly for this test
    req = DispatchRequest("telegram_direct", "msg:7", "document_agent", "read_only", "ref", "key7", "user:123", max_attempts=1)
    record = dispatch_svc.create_dispatch(req, "user:123")
    dispatch_svc.repository.update_dispatch_status(record.dispatch_id, 'failed', 'sys', expected_status='pending')
    dispatch_svc.repository.increment_attempt(record.dispatch_id, 'sys')

    with pytest.raises(RetryExhaustedError):
        dispatch_svc.retry_dispatch(record.dispatch_id, "user:123")


def test_conflicting_payload_ref_idempotency_replay_fails(dispatch_svc):
    first = DispatchRequest("telegram_direct", "same-source", "document_agent", "read_only", "ref-a", "same-key", "user:123")
    second = DispatchRequest("telegram_direct", "same-source", "document_agent", "read_only", "ref-b", "same-key", "user:123")
    dispatch_svc.create_dispatch(first, "user:123")
    with pytest.raises(DuplicateDispatchError):
        dispatch_svc.create_dispatch(second, "user:123")


def test_synchronize_stale_update_is_non_mutating(dispatch_svc):
    record = dispatch_svc.create_dispatch(DispatchRequest("telegram_direct", "sync", "document_agent", "read_only", "ref", "sync-key", "user:123"), "user:123")
    result = dispatch_svc.synchronize_status(record.dispatch_id, expected_status="running", observed_status="succeeded")
    assert result.applied is False
    assert result.status == "pending"


def test_adapter_timeout_transitions_to_timed_out(dispatch_svc, monkeypatch):
    from app.dispatch.models import DispatchResult

    class TimeoutAdapter:
        def execute(self, dispatch, attempt_number):
            return DispatchResult(dispatch.dispatch_id, attempt_number, False, "safe timeout", "2026-08-07T00:00:00+00:00", timed_out=True)

    monkeypatch.setattr("app.dispatch.service.get_adapter", lambda adapter_id: TimeoutAdapter())
    record = dispatch_svc.create_dispatch(DispatchRequest("telegram_direct", "timeout", "document_agent", "read_only", "ref", "timeout-key", "user:123"), "user:123")
    completed = dispatch_svc.dispatch(record.dispatch_id)
    assert completed.status == "timed_out"
    assert completed.result_summary == "safe timeout"
