"""ExecutionService — orchestrates the execution lifecycle for NOVA Sprint 3.

Responsibilities
----------------
- Sensitive-input guard (reuses SENSITIVE_CONTENT_PATTERN from app.security)
- State-machine enforcement (all transitions validated before DB write)
- Approval enforcement: authorized user, one execution, non-reusable
- Audit trail: every meaningful event is logged without sensitive content
- Duplicate-dispatch prevention: atomic CAS (compare-and-swap) via repository
- Restart reconciliation: running → failed on initialize(), one audit entry per
  reconciled execution
- Adapter invocation: LocalDeterministicAdapter only
- Limit enforcement: max_execution_seconds and max_output_bytes enforced here,
  not only within adapter simulation helpers
- No Telegram transport logic (handlers live in telegram_bot.py)
"""

from __future__ import annotations

import logging
import time

from app.execution.adapter import LocalDeterministicAdapter, hash_instruction
from app.execution.models import ExecutionRecord, ExecutionState
from app.execution.repository import (
    ApprovalError,
    ExecutionNotFoundError,
    ExecutionRepository,
    InvalidTransitionError,
)
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.router.classifier import classify_intent
from app.router.risk import assess_risk
from app.security import SENSITIVE_CONTENT_PATTERN

LOGGER = logging.getLogger(__name__)

_SENSITIVE_REJECTION_MESSAGE = (
    "Execution request rejected: the instruction contains sensitive content "
    "and cannot be processed. Remove credentials, keys, or secrets and retry."
)

OUTPUT_BYTE_LIMIT: int = 65_536
EXECUTION_TIMEOUT_SECONDS: int = 30


class SensitiveContentError(ValueError):
    """Raised when an instruction contains sensitive content."""


class ExecutionService:
    """Orchestrate the full execution lifecycle without exposing transport details."""

    def __init__(
        self,
        database: MemoryDatabase,
        authorized_user_id: int,
        adapter: LocalDeterministicAdapter | None = None,
    ) -> None:
        self._db = database
        self._authorized_user_id = authorized_user_id
        self._repo = ExecutionRepository(database)
        self._adapter = adapter or LocalDeterministicAdapter(
            max_output_bytes=OUTPUT_BYTE_LIMIT,
            timeout_seconds=EXECUTION_TIMEOUT_SECONDS,
        )

    # ---------------------------------------------------------------- init

    def initialize(self) -> None:
        """Apply schema additively and reconcile any running executions to failed.

        One audit entry is written per reconciled execution so the audit log
        reflects the state change.  Repeated initialization does not duplicate
        entries — reconcile_running_to_failed only matches state='running',
        which no longer applies after the first call.
        """
        self._repo.initialize()
        reconciled_ids = self._repo.reconcile_running_to_failed_with_ids()
        if reconciled_ids:
            LOGGER.warning(
                "Restart reconciliation: %d running execution(s) marked as failed.",
                len(reconciled_ids),
            )
            for execution_id in reconciled_ids:
                try:
                    self._repo.add_audit_entry(
                        execution_id=execution_id,
                        event="failed",
                        actor="system",
                        detail="Reconciled to failed on service restart.",
                    )
                except Exception:
                    LOGGER.warning(
                        "Could not write reconciliation audit entry for execution #%d.",
                        execution_id,
                    )

    # ------------------------------------------------------------------ run

    def submit(self, raw_instruction: str, requesting_user_id: int) -> ExecutionRecord:
        """Validate, classify, and create a new execution record.

        The raw instruction is NEVER stored. Only its SHA-256 hash is persisted.

        Raises
        ------
        SensitiveContentError
            If the instruction contains sensitive content.
        MemoryDatabaseError
            If the database operation fails.
        """
        self._guard_sensitive(raw_instruction)

        classification = classify_intent(raw_instruction)
        risk = assess_risk(
            classification.workflow_id, raw_instruction, classification.confidence
        )

        instruction_hash = hash_instruction(raw_instruction)
        actor = f"user:{requesting_user_id}"

        if risk.approval_mode == "REQUIRED":
            initial_state = ExecutionState.AWAITING_APPROVAL
        else:
            initial_state = ExecutionState.QUEUED

        record = self._repo.create_execution(
            instruction_hash=instruction_hash,
            risk_level=risk.risk_level,
            workflow_id=classification.workflow_id,
            initial_state=initial_state,
        )

        self._repo.add_audit_entry(
            execution_id=record.id,
            event="created",
            actor=actor,
            detail=(
                f"workflow={record.workflow_id} risk={record.risk_level} "
                f"state={record.state} hash_prefix={instruction_hash[:8]}"
            ),
        )

        if initial_state == ExecutionState.QUEUED:
            record = self._dispatch(record, actor)

        return record

    # ----------------------------------------------------------- approve

    def approve(self, execution_id: int, approving_user_id: int) -> ExecutionRecord:
        """Approve an awaiting_approval execution and dispatch it.

        Only the configured authorized user may approve.

        Raises
        ------
        ApprovalError
            If the approving user is not authorized or the execution is not
            in awaiting_approval state.
        ExecutionNotFoundError
            If the execution ID does not exist.
        """
        if approving_user_id != self._authorized_user_id:
            raise ApprovalError(
                f"User {approving_user_id} is not authorized to approve executions."
            )

        record = self._repo.get_execution(execution_id)
        if record.state != ExecutionState.AWAITING_APPROVAL:
            raise ApprovalError(
                f"Execution #{execution_id} is not awaiting approval "
                f"(current state: '{record.state}')"
            )

        actor = f"user:{approving_user_id}"
        record = self._repo.record_approval(execution_id, approving_user_id)

        self._repo.add_audit_entry(
            execution_id=execution_id,
            event="approved",
            actor=actor,
            detail=f"approved_by={approving_user_id} approved_at={record.approved_at}",
        )

        record = self._dispatch(record, actor)
        return record

    # --------------------------------------------------------------- cancel

    def cancel(self, execution_id: int, requesting_user_id: int) -> ExecutionRecord:
        """Cancel (or reject) a non-terminal execution.

        Cancellation always transitions to ``failed`` with event=``cancelled``.
        There is no separate 'cancelled' state — the six canonical states are
        the only valid states.

        For awaiting_approval: this is the rejection path (→ failed).
        For queued: cancel before dispatch (→ failed).
        For running: cooperative cancellation (→ failed).
        Terminal states cannot be cancelled.

        Raises
        ------
        InvalidTransitionError
            If the execution is in a terminal state.
        ExecutionNotFoundError
            If the execution ID does not exist.
        """
        record = self._repo.get_execution(execution_id)
        actor = f"user:{requesting_user_id}"

        if record.state in ExecutionState.TERMINAL:
            raise InvalidTransitionError(
                f"Execution #{execution_id} is terminal (state: '{record.state}') "
                "and cannot be cancelled."
            )

        prior_state = record.state
        record = self._repo.transition_state(
            execution_id=execution_id,
            expected_state=prior_state,
            new_state=ExecutionState.FAILED,
            result_summary=f"Cancelled by {actor}",
        )

        self._repo.add_audit_entry(
            execution_id=execution_id,
            event="cancelled",
            actor=actor,
            detail=f"cancelled from state={prior_state} (now failed)",
        )

        return record

    # --------------------------------------------------------------- status

    def get_status(self, execution_id: int) -> ExecutionRecord:
        """Return the current execution record.

        Raises ExecutionNotFoundError if the ID does not exist.
        """
        return self._repo.get_execution(execution_id)

    def list_audit(self, execution_id: int) -> list:
        """Return all audit entries for an execution in chronological order."""
        return self._repo.list_audit_entries(execution_id)

    # ------------------------------------------------------------- internal

    def _dispatch(self, record: ExecutionRecord, actor: str) -> ExecutionRecord:
        """Transition queued → running → completed|failed via the adapter.

        Limits enforced here (not only in adapter simulation helpers):
        - max_output_bytes: if exceeded, transition to failed.
        - max_execution_seconds: wall-clock timeout checked after adapter returns.
        """
        try:
            record = self._repo.transition_state(
                execution_id=record.id,
                expected_state=ExecutionState.QUEUED,
                new_state=ExecutionState.RUNNING,
            )
        except InvalidTransitionError:
            LOGGER.error(
                "Execution #%d: duplicate dispatch blocked (state was not queued).",
                record.id,
            )
            return self._repo.get_execution(record.id)

        self._repo.add_audit_entry(
            execution_id=record.id,
            event="running",
            actor="system",
            detail="Adapter dispatch started.",
        )

        start_time = time.monotonic()
        result = self._adapter.run(record.id, record.instruction_hash)
        elapsed = time.monotonic() - start_time

        # Enforce max_execution_seconds at the service layer
        if elapsed > EXECUTION_TIMEOUT_SECONDS:
            summary = (
                f"Execution exceeded time limit: {elapsed:.1f}s > "
                f"{EXECUTION_TIMEOUT_SECONDS}s."
            )
            record = self._repo.transition_state(
                execution_id=record.id,
                expected_state=ExecutionState.RUNNING,
                new_state=ExecutionState.FAILED,
                result_summary=summary,
            )
            self._repo.add_audit_entry(
                execution_id=record.id,
                event="failed",
                actor="system",
                detail=summary,
            )
            return record

        # Enforce max_output_bytes at the service layer
        if result.output_bytes > OUTPUT_BYTE_LIMIT:
            summary = (
                f"Output limit exceeded at service layer: {result.output_bytes} bytes > "
                f"{OUTPUT_BYTE_LIMIT} bytes."
            )
            record = self._repo.transition_state(
                execution_id=record.id,
                expected_state=ExecutionState.RUNNING,
                new_state=ExecutionState.FAILED,
                result_summary=summary,
            )
            self._repo.add_audit_entry(
                execution_id=record.id,
                event="failed",
                actor="system",
                detail=summary,
            )
            return record

        if result.success:
            final_state = ExecutionState.COMPLETED
            summary = result.summary
            event = "completed"
        else:
            final_state = ExecutionState.FAILED
            summary = result.summary
            event = "failed"

        record = self._repo.transition_state(
            execution_id=record.id,
            expected_state=ExecutionState.RUNNING,
            new_state=final_state,
            result_summary=summary,
        )

        self._repo.add_audit_entry(
            execution_id=record.id,
            event=event,
            actor="system",
            detail=summary,
        )

        return record

    # --------------------------------------------------------- guards

    @staticmethod
    def _guard_sensitive(text: str) -> None:
        """Raise SensitiveContentError if the text contains sensitive content.

        The raw text is never logged, printed, or included in the error message.
        """
        if SENSITIVE_CONTENT_PATTERN.search(text):
            raise SensitiveContentError(_SENSITIVE_REJECTION_MESSAGE)
