"""Canonical, single-approver approval authority."""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timezone

from app.dispatch.errors import ApprovalAuthorizationError, InvalidRequestError, InvalidTransitionError, StaleUpdateError
from app.dispatch.models import ApprovalDecision, ApprovalRequest
from app.dispatch.repository import DispatchRepository, utc_now
from app.dispatch.schema import apply_schema
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.security import SENSITIVE_CONTENT_PATTERN


_SENSITIVE_APPROVAL_TEXT = re.compile(r"(?:ssh-(?:rsa|ed25519)|-----BEGIN)", re.IGNORECASE)


class ApprovalService:
    def __init__(self, database: MemoryDatabase, *, authorized_user_id: int) -> None:
        self.database = database
        self.repository = DispatchRepository(database)
        self.authorized_user_id = authorized_user_id

    def initialize(self) -> None:
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
        except sqlite3.Error as error:
            raise MemoryDatabaseError("Approval schema initialization failed") from error

    @staticmethod
    def _safe_text(value: str, field: str, *, allow_empty: bool = False, limit: int = 500) -> str:
        if not isinstance(value, str):
            raise InvalidRequestError("Approval request is invalid.")
        cleaned = value.strip()
        if (not cleaned and not allow_empty) or len(cleaned) > limit or SENSITIVE_CONTENT_PATTERN.search(cleaned) or _SENSITIVE_APPROVAL_TEXT.search(cleaned):
            raise InvalidRequestError("Sensitive or invalid approval content was rejected.")
        return cleaned

    def request_approval(self, dispatch_id: str, requested_action: str, actor: str, *,
                         expires_at: str | None = None, correlation_id: str | None = None) -> ApprovalRequest:
        action = self._safe_text(requested_action, "requested action")
        if expires_at is not None:
            try:
                datetime.fromisoformat(expires_at)
            except ValueError as error:
                raise InvalidRequestError("Approval expiry is invalid.") from error
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.repository.get_dispatch_in(connection, dispatch_id)
            existing = self.repository.get_open_approval_in(connection, dispatch_id)
            if existing is not None:
                return existing
            approval = ApprovalRequest(str(uuid.uuid4()), dispatch_id, action, "requested", actor, utc_now(), expires_at=expires_at)
            self.repository.insert_approval_in(connection, approval, actor)
            return approval

    def approve(self, approval_id: str, approving_user_id: int) -> ApprovalDecision:
        if approving_user_id != self.authorized_user_id:
            raise ApprovalAuthorizationError("Approval is not authorized.")
        actor = f"user:{approving_user_id}"
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            approval = self.repository.get_approval_in(connection, approval_id)
            dispatch = self.repository.get_dispatch_in(connection, approval.dispatch_id)
            if approval.status != "requested" or dispatch.status != "awaiting_approval":
                raise StaleUpdateError("Approval is no longer pending.")
            if not self.repository.transition_approval_in(connection, approval_id, "requested", "approved", actor):
                raise StaleUpdateError("Approval update is stale.")
            if not self.repository.transition_dispatch_in(connection, dispatch.dispatch_id, "awaiting_approval", "approved", actor, event="approval_granted"):
                raise StaleUpdateError("Dispatch update is stale.")
            return ApprovalDecision(approval_id, dispatch.dispatch_id, "approved", actor, utc_now())

    def reject(self, approval_id: str, approving_user_id: int, reason: str) -> ApprovalDecision:
        if approving_user_id != self.authorized_user_id:
            raise ApprovalAuthorizationError("Approval is not authorized.")
        safe_reason = self._safe_text(reason, "rejection reason", allow_empty=True)
        actor = f"user:{approving_user_id}"
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            approval = self.repository.get_approval_in(connection, approval_id)
            dispatch = self.repository.get_dispatch_in(connection, approval.dispatch_id)
            if approval.status != "requested" or dispatch.status != "awaiting_approval":
                raise StaleUpdateError("Approval is no longer pending.")
            if not self.repository.transition_approval_in(connection, approval_id, "requested", "rejected", actor, safe_reason):
                raise StaleUpdateError("Approval update is stale.")
            if not self.repository.transition_dispatch_in(connection, dispatch.dispatch_id, "awaiting_approval", "rejected", actor, event="approval_rejected", detail=safe_reason):
                raise StaleUpdateError("Dispatch update is stale.")
            return ApprovalDecision(approval_id, dispatch.dispatch_id, "rejected", actor, utc_now())

    def get_approval(self, approval_id: str) -> ApprovalRequest:
        return self.repository.get_approval(approval_id)

    def list_pending(self, *, source_type: str | None = None) -> list[ApprovalRequest]:
        return self.repository.list_pending_approvals(source_type=source_type)

    def expire_or_close(self, approval_id: str, actor: str, reason: str) -> ApprovalDecision:
        safe_reason = self._safe_text(reason, "closure reason", allow_empty=True)
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            approval = self.repository.get_approval_in(connection, approval_id)
            if approval.status != "requested":
                raise StaleUpdateError("Approval is no longer pending.")
            dispatch = self.repository.get_dispatch_in(connection, approval.dispatch_id)
            if dispatch.status == "cancelled":
                target = "cancelled"
            elif approval.expires_at and datetime.fromisoformat(approval.expires_at) <= datetime.now(timezone.utc):
                target = "expired"
            else:
                raise InvalidTransitionError("Approval cannot be closed yet.")
            if not self.repository.transition_approval_in(connection, approval_id, "requested", target, actor, safe_reason):
                raise StaleUpdateError("Approval update is stale.")
            return ApprovalDecision(approval_id, approval.dispatch_id, target, actor, utc_now())
