from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.dispatch.approvals import ApprovalService
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.drafting.service import DraftingService
from app.memory.database import MemoryDatabase
from app.workspace_actions import WorkspaceActionService


class FakeDocsWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def create_private_document(self, title: str, body: str):
        self.calls.append((title, body))
        return type("Created", (), {"document_id": "doc-1"})()


class UncertainDocsWriter:
    def create_private_document(self, title: str, body: str):
        del title, body
        error = RuntimeError("provider response lost")
        error.submission_started = True
        raise error


def _services(tmp_path):
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    drafting = DraftingService(database)
    drafting.initialize()
    approvals = ApprovalService(database, authorized_user_id=7)
    approvals.initialize()
    dispatch = DispatchService(database, registry=AgentRegistry(), approvals=approvals)
    dispatch.initialize()
    writer = FakeDocsWriter()
    actions = WorkspaceActionService(database, drafting=drafting, dispatch=dispatch, approvals=approvals, docs_writer=writer)
    actions.initialize()
    return drafting, approvals, actions, writer


def test_docs_write_requires_exact_approved_action_and_replay_is_safe(tmp_path):
    drafting, approvals, actions, writer = _services(tmp_path)
    prepared = drafting.mark_ready_for_action(drafting.prepare_docs_memo("Memo", "Body", "user:7").id, "user:7")
    action = actions.request_create_docs_file(prepared.id, idempotency_key="docs-1", actor="user:7", correlation_id="corr-1")
    assert actions.execute(action.id).status == "awaiting_approval"
    assert writer.calls == []
    approvals.approve(action.approval_id or "", 7)
    assert actions.execute(action.id).status == "succeeded"
    assert actions.execute(action.id).status == "succeeded"
    assert writer.calls == [("Memo", prepared.body_text or "")]


def test_superseded_current_ready_source_never_calls_docs(tmp_path):
    drafting, approvals, actions, writer = _services(tmp_path)
    prepared = drafting.mark_ready_for_action(drafting.prepare_docs_memo("Memo", "Body", "user:7").id, "user:7")
    action = actions.request_create_docs_file(prepared.id, idempotency_key="docs-2", actor="user:7", correlation_id="corr-2")
    approvals.approve(action.approval_id or "", 7)
    with actions.database.connection() as connection:
        connection.execute("UPDATE prepared_workspace_actions SET status = 'superseded' WHERE id = ?", (prepared.id,))
        connection.execute("INSERT INTO prepared_workspace_actions (content_type, source_ref, title, body_text, body_payload, status, supersedes_id, created_by, created_at, updated_at) VALUES ('docs_memo', NULL, 'Memo 2', 'Body 2', NULL, 'ready_for_action', ?, 'user:7', ?, ?)", (prepared.id, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()))
    assert actions.execute(action.id).status == "cancelled"
    assert writer.calls == []


def test_ambiguous_post_submission_failure_requires_reconciliation(tmp_path):
    drafting, approvals, actions, _writer = _services(tmp_path)
    prepared = drafting.mark_ready_for_action(drafting.prepare_docs_memo("Memo", "Body", "user:7").id, "user:7")
    action = actions.request_create_docs_file(prepared.id, idempotency_key="docs-3", actor="user:7", correlation_id="corr-3")
    approvals.approve(action.approval_id or "", 7)
    actions.docs_writer = UncertainDocsWriter()

    assert actions.execute(action.id).status == "outcome_unknown"
    assert actions.execute(action.id).status == "outcome_unknown"


def test_approval_expiry_uses_injected_utc_clock_without_sleeps(tmp_path):
    now = [datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)]
    clock = lambda: now[0].isoformat()
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    drafting = DraftingService(database); drafting.initialize()
    approvals = ApprovalService(database, authorized_user_id=7, clock=clock); approvals.initialize()
    dispatch = DispatchService(database, registry=AgentRegistry(), approvals=approvals, clock=clock); dispatch.initialize()
    actions = WorkspaceActionService(database, drafting=drafting, dispatch=dispatch, approvals=approvals, docs_writer=FakeDocsWriter(), clock=clock); actions.initialize()
    prepared = drafting.mark_ready_for_action(drafting.prepare_docs_memo("Memo", "Body", "user:7").id, "user:7")
    action = actions.request_create_docs_file(prepared.id, idempotency_key="expiry", actor="user:7", correlation_id="expiry")
    now[0] += timedelta(minutes=14, seconds=59)
    approvals.approve(action.approval_id or "", 7)
    assert actions.execute(action.id).status == "succeeded"

    now[0] = datetime(2026, 8, 8, 13, 0, tzinfo=timezone.utc)
    second = actions.request_create_docs_file(prepared.id, idempotency_key="expiry-2", actor="user:7", correlation_id="expiry-2")
    now[0] += timedelta(minutes=15)
    with pytest.raises(Exception):
        approvals.approve(second.approval_id or "", 7)


def test_approval_chat_binding_fails_closed(tmp_path):
    drafting, _approvals, actions, _writer = _services(tmp_path)
    prepared = drafting.mark_ready_for_action(drafting.prepare_docs_memo("Memo", "Body", "user:7").id, "user:7")
    action = actions.request_create_docs_file(prepared.id, idempotency_key="chat", actor="user:7", correlation_id="chat", requested_chat_id="7")
    with pytest.raises(Exception):
        actions.approve_and_execute(action.approval_id or "", 7, chat_id="8")


class ThreadSafeDocsWriter:
    """Records concurrent calls with a lock, unlike FakeDocsWriter's bare list."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def create_private_document(self, title: str, body: str):
        with self._lock:
            self.calls.append((title, body))
        return type("Created", (), {"document_id": "doc-1"})()


def test_concurrent_execute_calls_make_exactly_one_docs_write(tmp_path):
    """Real multi-thread contention on the approved->executing CAS, not just
    sequential re-entrant calls: only one of many concurrently-racing
    executors may win the claim and call the Docs writer."""
    drafting, approvals, actions, _writer = _services(tmp_path)
    actions.docs_writer = writer = ThreadSafeDocsWriter()
    prepared = drafting.mark_ready_for_action(drafting.prepare_docs_memo("Memo", "Body", "user:7").id, "user:7")
    action = actions.request_create_docs_file(prepared.id, idempotency_key="concurrency-1", actor="user:7", correlation_id="c1")
    approvals.approve(action.approval_id or "", 7)

    results: list[str] = []
    errors: list[Exception] = []
    worker_count = 8
    barrier = threading.Barrier(worker_count)

    def run() -> None:
        barrier.wait()
        try:
            results.append(actions.execute(action.id).status)
        except Exception as error:  # pragma: no cover - failure surfaced via assertion below
            errors.append(error)

    threads = [threading.Thread(target=run) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert results == ["succeeded"] * worker_count
    assert len(writer.calls) == 1


def test_source_superseded_after_execution_claim_fails_closed_as_failed_not_cancelled(tmp_path):
    """The frozen lifecycle has no 'executing -> cancelled' edge; a
    freshness/integrity rejection discovered after the CAS claim to
    'executing' (but strictly before any provider call) must land on
    'failed', matching 'approved -> cancelled' being reserved for the
    pre-claim rejection instead."""
    drafting, approvals, actions, writer = _services(tmp_path)
    prepared = drafting.mark_ready_for_action(drafting.prepare_docs_memo("Memo", "Body", "user:7").id, "user:7")
    action = actions.request_create_docs_file(prepared.id, idempotency_key="post-claim-stale", actor="user:7", correlation_id="corr")
    approvals.approve(action.approval_id or "", 7)

    real_get_current_ready_action = drafting.get_current_ready_action
    call_count = {"n": 0}

    def flaky(action_id: int):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            return None
        return real_get_current_ready_action(action_id)

    drafting.get_current_ready_action = flaky
    try:
        result = actions.execute(action.id)
    finally:
        drafting.get_current_ready_action = real_get_current_ready_action

    assert result.status == "failed"
    assert writer.calls == []
