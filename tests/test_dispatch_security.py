import pytest
import os
import subprocess
from unittest.mock import patch
from app.dispatch.adapters import LocalDeterministicAgentAdapter
from app.dispatch.models import DispatchRecord
from app.dispatch.repository import utc_now

def test_local_deterministic_adapter_makes_no_external_calls():
    adapter = LocalDeterministicAgentAdapter()
    record = DispatchRecord(
        dispatch_id="test-123",
        source_type="telegram_direct",
        source_id="test",
        agent_id="document_agent",
        capability="read_only",
        payload_ref="ref",
        status="running",
        attempt_count=1,
        max_attempts=3,
        idempotency_key="key",
        correlation_id=None,
        requested_by="user:123",
        result_summary="",
        created_at=utc_now(),
        updated_at=utc_now()
    )

    with patch('os.system') as mock_os_system, \
         patch('subprocess.Popen') as mock_popen, \
         patch('subprocess.run') as mock_run:

        mock_os_system.side_effect = RuntimeError("External call prohibited")
        mock_popen.side_effect = RuntimeError("External call prohibited")
        mock_run.side_effect = RuntimeError("External call prohibited")

        adapter.execute(record)

        mock_os_system.assert_not_called()
        mock_popen.assert_not_called()
        mock_run.assert_not_called()

@pytest.mark.parametrize("value", [
    "-----BEGIN PRIVATE KEY-----", "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ", "api_key=abc123", '{"credential":"value"}',
])
def test_sensitive_dispatch_inputs_are_rejected_before_persistence(tmp_path, value):
    from app.memory.database import MemoryDatabase
    from app.dispatch.approvals import ApprovalService
    from app.dispatch.registry import AgentRegistry
    from app.dispatch.service import DispatchService
    from app.dispatch.models import DispatchRequest
    from app.dispatch.errors import InvalidRequestError

    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    approvals = ApprovalService(database, authorized_user_id=7)
    service = DispatchService(database, registry=AgentRegistry(), approvals=approvals)
    service.initialize()
    request = DispatchRequest("telegram_direct", "message-1", "document_agent", "read_only", value, "key-1", "user:7")
    with pytest.raises(InvalidRequestError):
        service.create_dispatch(request, "user:7")
    assert service.list_dispatches() == []
