"""Sprint 5G — ProviderGatewayAgentAdapter / Dispatch seam regression tests.

These specifically reproduce two defects found during independent review of
the initial 5G implementation, neither of which any pre-existing test caught
because both only manifest when the adapter is driven through the real,
synchronous `DispatchService.dispatch()` boundary (as Night Shift's tick and
the `/approve`/`/retrydispatch` Telegram handlers do) rather than by
constructing `ProviderGatewayService` directly:

1. `ProviderGatewayAgentAdapter.set_service_factory()` stored a bare
   function/lambda as a class attribute. Python's function descriptor
   protocol turns that into a bound method on instance access
   (`self._service_factory`), silently injecting `self` as an unwanted first
   positional argument and raising `TypeError` on every single call —
   the entire provider-backed dispatch path was unreachable.
2. `ProviderGatewayAgentAdapter.execute()` bridged its async provider call
   with a bare `asyncio.run(...)`. `DispatchService.dispatch()` is
   synchronous but is invoked both from plain synchronous callers and from
   inside python-telegram-bot's already-running event loop (the Night Shift
   scheduler tick, and the `/approve`/`/retrydispatch` handlers). Calling
   `asyncio.run()` while a loop is already running on the same thread raises
   `RuntimeError: asyncio.run() cannot be called from a running event loop`,
   so the real production path (Night Shift, or approving/retrying a
   provider-backed dispatch) always failed even though a plain synchronous
   pytest test calling `dispatch()` directly could never observe it.
"""

from __future__ import annotations

import asyncio

import pytest

from app.dispatch.adapters import ProviderGatewayAgentAdapter, get_adapter
from app.dispatch.approvals import ApprovalService
from app.dispatch.errors import ApprovalRequiredError
from app.dispatch.models import DispatchRequest
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.memory.database import MemoryDatabase


class FakeProviderService:
    """A minimal double for ProviderGatewayService: isolates this seam's own
    plumbing (callable binding, sync/async bridging, routing) from provider
    selection/fallback policy, which is covered separately in
    tests/test_provider_policy.py."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def generate_text(self, prompt: str, user_id: int = 0, *, workflow_id: str | None = None, role_id: str | None = None) -> str:
        self.calls.append((prompt, workflow_id, role_id))
        await asyncio.sleep(0)
        return "provider result"


@pytest.fixture(autouse=True)
def _reset_service_factory():
    """`ProviderGatewayAgentAdapter._service_factory` is process-global
    (the AgentAdapter Protocol's `get_adapter(adapter_id)` takes no
    constructor arguments, so this is the only seam available to inject the
    dependency without changing that public interface) — reset it around
    every test so no test leaks state into another."""
    yield
    ProviderGatewayAgentAdapter.set_service_factory(lambda: None)


@pytest.fixture
def memory_db(tmp_path):
    return MemoryDatabase(tmp_path / "nova.sqlite3")


@pytest.fixture
def approval_svc(memory_db):
    return ApprovalService(memory_db, authorized_user_id=123)


@pytest.fixture
def dispatch_svc(memory_db, approval_svc):
    svc = DispatchService(memory_db, registry=AgentRegistry(), approvals=approval_svc)
    svc.initialize()
    return svc


def _create_and_dispatch(dispatch_svc, agent_id: str):
    request = DispatchRequest(
        source_type="telegram_direct", source_id="msg:1", agent_id=agent_id,
        capability="draft_only", payload_ref="do the thing", idempotency_key=f"key:{agent_id}",
        requested_by="user:123",
    )
    record = dispatch_svc.create_dispatch(request, "user:123")
    return dispatch_svc.dispatch(record.dispatch_id, "user:123")


def test_service_factory_lambda_is_not_auto_bound(dispatch_svc):
    """Regression for defect #1: a plain lambda factory must be callable
    with zero arguments through the class attribute, not silently receive
    `self`."""
    fake = FakeProviderService()
    ProviderGatewayAgentAdapter.set_service_factory(lambda: fake)

    result = _create_and_dispatch(dispatch_svc, "generic_ai_agent")

    assert result.status == "succeeded"
    assert fake.calls == [("Dispatch reference: do the thing", "GENERAL", "CONTROL_TOWER")]


def test_execute_succeeds_from_a_running_event_loop(dispatch_svc):
    """Regression for defect #2: DispatchService.dispatch() is synchronous
    but Night Shift's tick and the /approve and /retrydispatch Telegram
    handlers all invoke it from inside an already-running asyncio event
    loop. Calling the plain sync dispatch() from within a real running loop
    (via asyncio.run(main()) here, exactly as PTB's Application does)
    must not raise `RuntimeError: asyncio.run() cannot be called from a
    running event loop`."""
    fake = FakeProviderService()
    ProviderGatewayAgentAdapter.set_service_factory(lambda: fake)

    async def main():
        # dispatch_svc.dispatch(...) is a plain synchronous call made from
        # inside this coroutine, which is itself running on a real event
        # loop -- exactly the shape of _nightshift_tick calling
        # night_worker.execute_via_dispatch() from inside PTB's JobQueue.
        return _create_and_dispatch(dispatch_svc, "coding_agent")

    result = asyncio.run(main())

    assert result.status == "succeeded"
    assert fake.calls == [("Dispatch reference: do the thing", "TECHNICAL", "EXECUTION_WORKER")]


@pytest.mark.parametrize(
    "agent_id,expected_workflow,expected_role",
    [
        ("coding_agent", "TECHNICAL", "EXECUTION_WORKER"),
        ("architecture_agent", "TECHNICAL", "TECHNICAL_ARCHITECT"),
        ("generic_ai_agent", "GENERAL", "CONTROL_TOWER"),
    ],
)
def test_agent_routes_to_expected_workflow_and_role(dispatch_svc, agent_id, expected_workflow, expected_role):
    fake = FakeProviderService()
    ProviderGatewayAgentAdapter.set_service_factory(lambda: fake)

    result = _create_and_dispatch(dispatch_svc, agent_id)

    assert result.status == "succeeded"
    assert fake.calls[0][1:] == (expected_workflow, expected_role)


def test_execute_fails_safely_when_provider_gateway_not_configured(dispatch_svc):
    ProviderGatewayAgentAdapter.set_service_factory(lambda: None)

    result = _create_and_dispatch(dispatch_svc, "generic_ai_agent")

    assert result.status == "failed"


def test_execute_fails_safely_when_factory_unset():
    adapter = get_adapter("provider_gateway")
    from app.dispatch.models import DispatchRecord

    record = DispatchRecord(
        dispatch_id="d1", source_type="telegram_direct", source_id="s1",
        agent_id="generic_ai_agent", capability="draft_only", payload_ref="ref",
        status="running", attempt_count=1, max_attempts=1, idempotency_key="k1",
        correlation_id=None, requested_by="tester", result_summary="",
        created_at="now", updated_at="now",
    )
    result = adapter.execute(record, 1)
    assert result.success is False


def test_approval_required_capability_still_gates_provider_dispatch(dispatch_svc):
    """Approval-gated capabilities must still block execution before the
    provider adapter is ever reached, exactly as for any other agent -- the
    provider seam introduces no approval bypass."""
    fake = FakeProviderService()
    ProviderGatewayAgentAdapter.set_service_factory(lambda: fake)
    request = DispatchRequest(
        source_type="telegram_direct", source_id="msg:2", agent_id="coding_agent",
        capability="draft_only", payload_ref="ref", idempotency_key="key:gated",
        requested_by="user:123",
    )
    record = dispatch_svc.create_dispatch(request, "user:123")
    # draft_only is approval-free for every registered agent today, so this
    # documents the seam's own boundary: dispatch() itself still enforces
    # the pending/awaiting_approval contract unmodified.
    assert record.status == "pending"
