"""The single approval-gated Docs creation vertical slice."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from app.dispatch.models import DispatchRequest
from app.drafting.service import DraftingService
from app.memory.database import MemoryDatabase
from app.workspace_actions.models import WORKSPACE_ACTION_TYPE, WorkspaceAction
from app.workspace_actions.repository import WorkspaceActionRepository
from app.workspace_actions.schema import apply_schema


class WorkspaceActionError(Exception):
    """Safe failure for approval-gated Workspace actions."""


class DocsWriteCapabilityUnavailable(WorkspaceActionError):
    """Raised before submission when the configured Docs write capability is absent."""


class UnavailableDocsWriter:
    """Startup-safe capability boundary; it never initiates OAuth or a network call."""

    def create_private_document(self, title: str, body_text: str):
        del title, body_text
        raise DocsWriteCapabilityUnavailable("Google Docs write capability is unavailable.")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceActionService:
    def __init__(self, database: MemoryDatabase, *, drafting: DraftingService, dispatch, approvals, docs_writer, clock: Callable[[], str] = _utc_now, account_namespace: str | None = None) -> None:
        self.database, self.drafting, self.dispatch, self.approvals, self.docs_writer = database, drafting, dispatch, approvals, docs_writer
        self.repository, self.clock, self.account_namespace = WorkspaceActionRepository(database), clock, account_namespace

    def initialize(self) -> None:
        with self.database.connection() as connection:
            apply_schema(connection)

    def get_action(self, action_id: int) -> WorkspaceAction | None:
        return self.repository.get(action_id)

    def get_action_for_approval(self, approval_id: str) -> WorkspaceAction | None:
        return self.repository.get_by_approval(approval_id)

    def approve_and_execute(self, approval_id: str, approving_user_id: int, *, chat_id: str | None = None) -> WorkspaceAction | None:
        """Use canonical approval, then execute only the bound WorkspaceAction."""
        action = self.get_action_for_approval(approval_id)
        if action is None:
            return None
        if action.requested_chat_id is not None and action.requested_chat_id != chat_id:
            raise WorkspaceActionError("Approval chat binding is invalid.")
        decision = self.approvals.approve(approval_id, approving_user_id)
        if decision.dispatch_id != action.dispatch_id:
            raise WorkspaceActionError("Approval is not bound to this workspace action.")
        return self.execute(action.id, actor=f"user:{approving_user_id}")

    def reject(self, approval_id: str, approving_user_id: int, reason: str, *, chat_id: str | None = None) -> WorkspaceAction | None:
        action = self.get_action_for_approval(approval_id)
        if action is None:
            return None
        if action.requested_chat_id is not None and action.requested_chat_id != chat_id:
            raise WorkspaceActionError("Approval chat binding is invalid.")
        decision = self.approvals.reject(approval_id, approving_user_id, reason)
        if decision.dispatch_id != action.dispatch_id:
            raise WorkspaceActionError("Approval is not bound to this workspace action.")
        return self.observe_approval(action.id, actor=f"user:{approving_user_id}")

    @staticmethod
    def _fingerprint(action_id: int, prepared, account_namespace: str | None) -> str:
        snapshot = {
            "schema_version": 1, "workspace_action_id": action_id, "action_type": WORKSPACE_ACTION_TYPE,
            "prepared_action": {"id": prepared.id, "content_type": prepared.content_type, "status": prepared.status, "supersedes_id": prepared.supersedes_id},
            "title": prepared.title or "", "body_text": prepared.body_text or "", "account_namespace": account_namespace,
            "creation_mode": "private", "risk_classification": "MODERATE_EXTERNAL_WRITE",
        }
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()

    def request_create_docs_file(self, prepared_action_id: int, *, idempotency_key: str, actor: str, correlation_id: str, requested_chat_id: str | None = None) -> WorkspaceAction:
        prepared = self.drafting.get_current_ready_action(prepared_action_id)
        if prepared is None:
            raise WorkspaceActionError("Prepared action is not current and ready.")
        existing = self.repository.get_by_idempotency(idempotency_key)
        if existing is not None:
            return existing
        now = self.clock()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            action = self.repository.insert_in(connection, {
                "action_type": WORKSPACE_ACTION_TYPE, "prepared_action_id": prepared.id, "dispatch_id": None, "approval_id": None,
                "payload_schema_version": 1, "payload_fingerprint": "", "account_namespace": self.account_namespace,
                "idempotency_key": idempotency_key, "correlation_id": correlation_id, "requested_chat_id": requested_chat_id, "status": "prepared", "version": 1,
                "provider_reference": None, "failure_category": None, "requested_at": now, "approved_at": None,
                "executing_at": None, "completed_at": None, "updated_at": now,
            })
        fingerprint = self._fingerprint(action.id, prepared, self.account_namespace)
        expiry = (datetime.fromisoformat(now) + timedelta(minutes=15)).isoformat()
        dispatch = self.dispatch.create_dispatch(DispatchRequest(
            "workspace_action", str(action.id), "workspace_agent", "workspace_write", f"workspace_action:{action.id}",
            idempotency_key, actor, correlation_id, 1,
            f"1. Create private Google Doc #{action.id}", expiry, now,
        ), actor)
        approval = self.approvals.list_pending(source_type="workspace_action")
        linked = next((item for item in approval if item.dispatch_id == dispatch.dispatch_id), None)
        if linked is None:
            raise WorkspaceActionError("Canonical approval was not created.")
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.repository.get_in(connection, action.id)
            assert current is not None
            if not self.repository.cas_in(connection, current, "prepared", "awaiting_approval", self.clock(), dispatch_id=dispatch.dispatch_id, approval_id=linked.approval_id, payload_fingerprint=fingerprint):
                raise WorkspaceActionError("Workspace action linkage is stale.")
            latest = self.repository.get_in(connection, action.id)
            assert latest is not None
            self.repository.audit_in(connection, latest, "approval_requested", actor, self.clock())
            return latest

    def observe_approval(self, action_id: int, *, actor: str = "system:approval") -> WorkspaceAction:
        action = self.repository.get(action_id)
        if action is None or action.approval_id is None:
            raise WorkspaceActionError("Workspace action was not found.")
        approval = self.approvals.get_approval(action.approval_id)
        if approval.dispatch_id != action.dispatch_id:
            raise WorkspaceActionError("Approval dispatch binding is invalid.")
        target = "approved" if approval.status == "approved" else "rejected" if approval.status in {"rejected", "cancelled", "expired"} else None
        if target is None or action.status != "awaiting_approval":
            return action
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.repository.get_in(connection, action.id)
            assert current is not None
            if self.repository.cas_in(connection, current, "awaiting_approval", target, self.clock(), approved_at=self.clock() if target == "approved" else None):
                current = self.repository.get_in(connection, action.id)
                assert current is not None
                self.repository.audit_in(connection, current, f"approval_{target}", actor, self.clock())
            return current

    def execute(self, action_id: int, *, actor: str = "system:workspace_action") -> WorkspaceAction:
        action = self.observe_approval(action_id, actor=actor)
        if action.status != "approved":
            return action
        source = self.drafting.get_current_ready_action(action.prepared_action_id)
        if source is None or self._fingerprint(action.id, source, action.account_namespace) != action.payload_fingerprint:
            return self._finalize(action, "cancelled", actor, failure_category="freshness_or_integrity")
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.repository.get_in(connection, action.id)
            assert current is not None
            if not self.repository.cas_in(connection, current, "approved", "executing", self.clock(), executing_at=self.clock()):
                return self.repository.get_in(connection, action.id) or current
            claimed = self.repository.get_in(connection, action.id)
            assert claimed is not None
            self.repository.audit_in(connection, claimed, "execution_claimed", actor, self.clock())
        source = self.drafting.get_current_ready_action(claimed.prepared_action_id)
        if source is None or self._fingerprint(claimed.id, source, claimed.account_namespace) != claimed.payload_fingerprint:
            # The frozen lifecycle has no "executing -> cancelled" edge; only
            # "executing -> failed" is valid pre-submission (no provider call
            # has happened yet at this point), unlike the "approved ->
            # cancelled" rejection above which occurs before the CAS claim.
            return self._finalize(claimed, "failed", actor, failure_category="freshness_or_integrity")
        try:
            result = self.docs_writer.create_private_document(source.title or "Untitled", source.body_text or "")
        except Exception as error:
            category = "outcome_unknown" if getattr(error, "submission_started", False) else "failed"
            return self._finalize(claimed, category, actor, failure_category=type(error).__name__)
        return self._finalize(claimed, "succeeded", actor, provider_reference=getattr(result, "document_id", None))

    def _finalize(self, action: WorkspaceAction, target: str, actor: str, *, provider_reference: str | None = None, failure_category: str | None = None) -> WorkspaceAction:
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.repository.get_in(connection, action.id)
            assert current is not None
            if current.status != "executing" and target in {"succeeded", "failed", "outcome_unknown"}:
                return current
            expected = "executing" if current.status == "executing" else current.status
            if self.repository.cas_in(connection, current, expected, target, self.clock(), completed_at=self.clock(), provider_reference=provider_reference, failure_category=failure_category):
                current = self.repository.get_in(connection, action.id)
                assert current is not None
                self.repository.audit_in(connection, current, f"execution_{target}", actor, self.clock(), failure_category or "")
            return current
