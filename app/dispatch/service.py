"""Controlled dispatch state machine and adapter boundary."""

from __future__ import annotations

import logging
import re
import sqlite3
import uuid

from app.dispatch.adapters import get_adapter
from app.dispatch.approvals import ApprovalService
from app.dispatch.errors import (
    ApprovalRequiredError, CancellationError, DispatchError, DispatchTimeoutError,
    DuplicateDispatchError, ExecutionFailureError, InvalidRequestError,
    InvalidTransitionError, RetryExhaustedError, StaleUpdateError,
)
from app.dispatch.models import CancellationResult, DispatchRecord, DispatchRequest, StatusSyncResult
from app.dispatch.registry import AgentRegistry
from app.dispatch.repository import DispatchRepository, utc_now
from app.dispatch.schema import apply_schema
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.security import SENSITIVE_CONTENT_PATTERN

LOGGER = logging.getLogger(__name__)

_SOURCE_TYPES = frozenset({"control_tower_work_item", "night_shift_job", "telegram_direct"})
_APPROVAL_FREE = frozenset({"read_only", "draft_only"})
_APPROVAL_REQUIRED = frozenset({"external_communication", "publication"})
_TRANSITIONS = {
    "pending": frozenset({"dispatching", "awaiting_approval", "cancelled"}),
    "awaiting_approval": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"dispatching", "cancelled"}),
    "dispatching": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "timed_out", "cancelled"}),
    "failed": frozenset({"dispatching"}),
    "timed_out": frozenset({"dispatching"}),
}
_TERMINAL = frozenset({"succeeded", "cancelled", "rejected"})
_OPAQUE_REFERENCE_BLOCKLIST = re.compile(r"(?:ssh-(?:rsa|ed25519)|-----BEGIN|\b(?:token|credential|secret|password)\b)", re.IGNORECASE)


class DispatchService:
    def __init__(self, database: MemoryDatabase, *, registry: AgentRegistry, approvals: ApprovalService) -> None:
        self.database = database
        self.repository = DispatchRepository(database)
        self.registry = registry
        self.approvals = approvals

    def initialize(self) -> None:
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
        except sqlite3.Error as error:
            raise MemoryDatabaseError("Dispatch schema initialization failed") from error

    @staticmethod
    def _safe(value: str | None, field: str, *, limit: int = 200, allow_empty: bool = False) -> str:
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise InvalidRequestError("Dispatch request is invalid.")
        value = value.strip()
        if (not value and not allow_empty) or len(value) > limit or SENSITIVE_CONTENT_PATTERN.search(value) or _OPAQUE_REFERENCE_BLOCKLIST.search(value):
            raise InvalidRequestError("Sensitive or invalid dispatch content was rejected.")
        return value

    def _validate_request(self, request: DispatchRequest) -> DispatchRequest:
        if request.source_type not in _SOURCE_TYPES:
            raise InvalidRequestError("Dispatch source is invalid.")
        source_id = self._safe(request.source_id, "source id")
        agent_id = self._safe(request.agent_id, "agent id")
        capability = self._safe(request.capability, "capability")
        payload_ref = self._safe(request.payload_ref, "payload reference", allow_empty=True, limit=128)
        key = self._safe(request.idempotency_key, "idempotency key", limit=200)
        requested_by = self._safe(request.requested_by, "actor")
        correlation_id = self._safe(request.correlation_id, "correlation id", allow_empty=True, limit=200) or None
        if not isinstance(request.max_attempts, int) or isinstance(request.max_attempts, bool) or not 1 <= request.max_attempts <= 10:
            raise InvalidRequestError("Dispatch retry limit is invalid.")
        self.registry.validate_capability(agent_id, capability)
        if capability not in _APPROVAL_FREE | _APPROVAL_REQUIRED:
            raise InvalidRequestError("Dispatch capability requires an explicit policy classification.")
        return DispatchRequest(request.source_type, source_id, agent_id, capability, payload_ref, key, requested_by, correlation_id, request.max_attempts)

    @staticmethod
    def _same_request(existing: DispatchRecord, request: DispatchRequest) -> bool:
        return (
            existing.source_type == request.source_type and existing.source_id == request.source_id and
            existing.agent_id == request.agent_id and existing.capability == request.capability and
            existing.payload_ref == request.payload_ref and existing.requested_by == request.requested_by and
            existing.correlation_id == request.correlation_id and existing.max_attempts == request.max_attempts
        )

    def create_dispatch(self, request: DispatchRequest, actor: str) -> DispatchRecord:
        request = self._validate_request(request)
        actor = self._safe(actor, "actor")
        existing = self.repository.find_idempotent(request)
        if existing is not None:
            if self._same_request(existing, request):
                return existing
            raise DuplicateDispatchError("Idempotency key conflicts with an existing dispatch.")
        status = "pending" if request.capability in _APPROVAL_FREE else "awaiting_approval"
        now = utc_now()
        record = DispatchRecord(str(uuid.uuid4()), request.source_type, request.source_id, request.agent_id,
                                request.capability, request.payload_ref, status, 0, request.max_attempts,
                                request.idempotency_key, request.correlation_id, request.requested_by, "", now, now)
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.repository.insert_dispatch_in(connection, record, actor)
            if status == "awaiting_approval":
                from app.dispatch.models import ApprovalRequest
                approval = ApprovalRequest(str(uuid.uuid4()), record.dispatch_id,
                    f"Approve {record.capability} for {record.agent_id}", "requested", actor, utc_now())
                self.repository.insert_approval_in(connection, approval, actor)
        return record

    def _transition(self, dispatch_id: str, expected: str, target: str, actor: str, *, event: str = "status_changed",
                    summary: str | None = None) -> None:
        if target not in _TRANSITIONS.get(expected, frozenset()):
            raise InvalidTransitionError("Dispatch state transition is not allowed.")
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self.repository.transition_dispatch_in(connection, dispatch_id, expected, target, actor, event=event, result_summary=summary):
                raise StaleUpdateError("Dispatch update is stale.")

    def dispatch(self, dispatch_id: str, actor: str = "system:dispatch") -> DispatchRecord:
        record = self.repository.get_dispatch(dispatch_id)
        if record.status == "awaiting_approval":
            raise ApprovalRequiredError("Dispatch requires explicit approval.")
        if record.status not in {"pending", "approved"}:
            raise InvalidTransitionError("Dispatch is not ready to run.")
        self._transition(dispatch_id, record.status, "dispatching", actor, event="dispatch_started")
        self._transition(dispatch_id, "dispatching", "running", actor, event="adapter_claimed")
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            attempt = self.repository.increment_attempt_in(connection, dispatch_id, "running", actor)
            if attempt < 0:
                raise StaleUpdateError("Dispatch update is stale.")
        running = self.repository.get_dispatch(dispatch_id)
        adapter = get_adapter(self.registry.resolve_agent(running.agent_id).adapter_id)
        try:
            result = adapter.execute(running, attempt)
            summary = self._safe(result.summary, "result summary", allow_empty=True, limit=500)
            target = "timed_out" if result.timed_out else "succeeded" if result.success else "failed"
        except DispatchTimeoutError:
            summary, target = "Dispatch timed out safely.", "timed_out"
        except DispatchError:
            summary, target = "Dispatch failed safely.", "failed"
        except Exception:
            LOGGER.exception("Unexpected adapter failure during dispatch %s.", dispatch_id)
            summary, target = "Dispatch failed safely.", "failed"
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.repository.finish_attempt_in(connection, dispatch_id, attempt, target, summary)
            if not self.repository.transition_dispatch_in(connection, dispatch_id, "running", target, actor, event="adapter_completed", result_summary=summary):
                raise StaleUpdateError("Dispatch update is stale.")
        return self.repository.get_dispatch(dispatch_id)

    def get_dispatch(self, dispatch_id: str) -> DispatchRecord:
        return self.repository.get_dispatch(dispatch_id)

    def list_dispatches(self, *, status: str | None = None, source_type: str | None = None,
                        agent_id: str | None = None, limit: int = 50) -> list[DispatchRecord]:
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise InvalidRequestError("Dispatch list limit is invalid.")
        return self.repository.list_dispatches(status=status, source_type=source_type, agent_id=agent_id, limit=limit)

    def cancel_dispatch(self, dispatch_id: str, actor: str, reason: str) -> CancellationResult:
        actor = self._safe(actor, "actor")
        reason = self._safe(reason, "cancellation reason", allow_empty=True, limit=500)
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self.repository.get_dispatch_in(connection, dispatch_id)
            if record.status in _TERMINAL:
                raise CancellationError("Dispatch is already terminal.")
            if not self.repository.transition_dispatch_in(connection, dispatch_id, record.status, "cancelled", actor, event="cancelled", detail=reason):
                raise StaleUpdateError("Dispatch update is stale.")
            pending = self.repository.get_open_approval_in(connection, dispatch_id)
            if pending is not None and not self.repository.transition_approval_in(connection, pending.approval_id, "requested", "cancelled", actor, reason):
                raise StaleUpdateError("Approval update is stale.")
            return CancellationResult(dispatch_id, "cancelled", utc_now())

    def retry_dispatch(self, dispatch_id: str, actor: str) -> DispatchRecord:
        record = self.repository.get_dispatch(dispatch_id)
        if record.status not in {"failed", "timed_out"}:
            raise InvalidTransitionError("Dispatch is not retryable.")
        if record.attempt_count >= record.max_attempts:
            raise RetryExhaustedError("Dispatch retry limit has been reached.")
        self._transition(dispatch_id, record.status, "dispatching", actor, event="retry_started")
        self._transition(dispatch_id, "dispatching", "running", actor, event="adapter_claimed")
        # Execute from the already-running retry without reopening another transition.
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            attempt = self.repository.increment_attempt_in(connection, dispatch_id, "running", actor)
        running = self.repository.get_dispatch(dispatch_id)
        try:
            result = get_adapter(self.registry.resolve_agent(running.agent_id).adapter_id).execute(running, attempt)
            summary = self._safe(result.summary, "result summary", allow_empty=True, limit=500)
            target = "timed_out" if result.timed_out else "succeeded" if result.success else "failed"
        except DispatchTimeoutError:
            summary, target = "Dispatch timed out safely.", "timed_out"
        except Exception:
            LOGGER.exception("Unexpected adapter failure during retry of dispatch %s.", dispatch_id)
            summary, target = "Dispatch failed safely.", "failed"
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.repository.finish_attempt_in(connection, dispatch_id, attempt, target, summary)
            if not self.repository.transition_dispatch_in(connection, dispatch_id, "running", target, actor, event="adapter_completed", result_summary=summary):
                raise StaleUpdateError("Dispatch update is stale.")
        return self.repository.get_dispatch(dispatch_id)

    def synchronize_status(self, dispatch_id: str, *, expected_status: str, observed_status: str,
                           actor: str = "system:sync", correlation_id: str | None = None) -> StatusSyncResult:
        aliases = {"pending": "pending", "running": "running", "succeeded": "succeeded", "failed": "failed", "timed_out": "timed_out", "cancelled": "cancelled"}
        if observed_status not in aliases:
            raise InvalidRequestError("Observed dispatch status is invalid.")
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self.repository.get_dispatch_in(connection, dispatch_id)
            if record.status != expected_status:
                return StatusSyncResult(dispatch_id, False, record.status, record.updated_at)
            if observed_status == record.status:
                return StatusSyncResult(dispatch_id, True, record.status, record.updated_at)
            if observed_status not in _TRANSITIONS.get(record.status, frozenset()):
                return StatusSyncResult(dispatch_id, False, record.status, record.updated_at)
            applied = self.repository.transition_dispatch_in(connection, dispatch_id, expected_status, observed_status, actor,
                                                              event="synchronized", correlation_id=correlation_id)
            latest = self.repository.get_dispatch_in(connection, dispatch_id)
            return StatusSyncResult(dispatch_id, applied, latest.status, latest.updated_at)
