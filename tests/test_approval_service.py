import pytest
from app.memory.database import MemoryDatabase
from app.dispatch.approvals import ApprovalService
from app.dispatch.service import DispatchService
from app.dispatch.registry import AgentRegistry
from app.dispatch.models import DispatchRequest
from app.dispatch.errors import ApprovalAuthorizationError, StaleUpdateError, InvalidTransitionError

@pytest.fixture
def memory_db(tmp_path):
    db = MemoryDatabase(tmp_path / "nova.sqlite3")
    return db

@pytest.fixture
def approval_svc(memory_db):
    svc = ApprovalService(memory_db, authorized_user_id=123)
    svc.initialize()
    return svc

@pytest.fixture
def dispatch_svc(memory_db, approval_svc):
    svc = DispatchService(memory_db, registry=AgentRegistry(), approvals=approval_svc)
    svc.initialize()
    return svc

def test_request_approval_idempotent(dispatch_svc, approval_svc):
    req = DispatchRequest("telegram_direct", "msg:1", "procurement_agent", "external_communication", "ref", "key1", "user:123")
    record = dispatch_svc.create_dispatch(req, "user:123")

    req1 = approval_svc.request_approval(record.dispatch_id, "test", "user:123")
    req2 = approval_svc.request_approval(record.dispatch_id, "test", "user:123")

    assert req1.approval_id == req2.approval_id

def test_approve_authorization(dispatch_svc, approval_svc):
    req = DispatchRequest("telegram_direct", "msg:2", "procurement_agent", "external_communication", "ref", "key2", "user:123")
    record = dispatch_svc.create_dispatch(req, "user:123")
    approval = approval_svc.list_pending()[0]

    with pytest.raises(ApprovalAuthorizationError):
        approval_svc.approve(approval.approval_id, approving_user_id=999)

def test_approve_success(dispatch_svc, approval_svc):
    req = DispatchRequest("telegram_direct", "msg:3", "procurement_agent", "external_communication", "ref", "key3", "user:123")
    record = dispatch_svc.create_dispatch(req, "user:123")
    approval = approval_svc.list_pending()[0]

    decision = approval_svc.approve(approval.approval_id, approving_user_id=123)
    assert decision.status == "approved"

    # Cannot approve again
    with pytest.raises(StaleUpdateError):
        approval_svc.approve(approval.approval_id, approving_user_id=123)

def test_reject_success(dispatch_svc, approval_svc):
    req = DispatchRequest("telegram_direct", "msg:4", "procurement_agent", "external_communication", "ref", "key4", "user:123")
    record = dispatch_svc.create_dispatch(req, "user:123")
    approval = approval_svc.list_pending()[0]

    decision = approval_svc.reject(approval.approval_id, approving_user_id=123, reason="no")
    assert decision.status == "rejected"

    disp = dispatch_svc.get_dispatch(record.dispatch_id)
    assert disp.status == "rejected"


def test_cancel_pending_dispatch_closes_linked_approval(dispatch_svc, approval_svc):
    request = DispatchRequest("telegram_direct", "msg:cancel", "procurement_agent", "external_communication", "ref", "cancel-key", "user:123")
    dispatch = dispatch_svc.create_dispatch(request, "user:123")
    approval = approval_svc.list_pending()[0]

    dispatch_svc.cancel_dispatch(dispatch.dispatch_id, "user:123", "operator cancelled")

    assert dispatch_svc.get_dispatch(dispatch.dispatch_id).status == "cancelled"
    assert approval_svc.get_approval(approval.approval_id).status == "cancelled"
    with pytest.raises(StaleUpdateError):
        approval_svc.approve(approval.approval_id, 123)


def test_sensitive_rejection_reason_is_rejected_before_audit(dispatch_svc, approval_svc):
    from app.dispatch.errors import InvalidRequestError
    request = DispatchRequest("telegram_direct", "reason-sensitive", "procurement_agent", "external_communication", "ref", "reason-key", "user:123")
    dispatch_svc.create_dispatch(request, "user:123")
    approval = approval_svc.list_pending()[0]
    with pytest.raises(InvalidRequestError):
        approval_svc.reject(approval.approval_id, 123, "api_key=secret")
    assert approval_svc.get_approval(approval.approval_id).status == "requested"
