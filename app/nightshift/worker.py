"""Sprint 5F: Night Shift Worker — safe automated execution of overnight tasks.

This worker is a consumer of Sprint 5B.1's `app/dispatch/` interfaces
(`DispatchService`, `ApprovalService`, `AgentRegistry`) and of Sprint 5A.1's
`NightShiftService`. It owns no dispatch/approval state of its own and never
writes `night_queue_jobs.status` directly — every status change is made
through `NightShiftService.transition_night_job()`, which validates against
the unmodified 5A.1 `JOB_TRANSITIONS` state machine. Only lease, dispatch
linkage, approval linkage, attempt-count, and eligible-after metadata are
ever written directly by repository helpers (see `NightShiftRepository`'s
Sprint 5F methods), and none of those touch `status`.

Known state-model limitation (see docs/night-shift-runtime.md): 5A.1's
`JOB_TRANSITIONS` only reaches `completed` via `awaiting_approval`, which
requires a real human decision. An approval-free automated job whose
dispatch succeeds therefore cannot be marked `completed` without either
faking approval or altering the shared state machine — this sprint does
neither. Instead such a job is advanced, through legal transitions only, to
`draft_saved` (the last state 5A.1 already defines as "the draft/work is
ready"), and the real outcome (dispatch succeeded, with `dispatch_id`
attached) is recorded via `night_shift_audit_log` and an
`attention_required` notification so it is visible in `/nightqueue`,
`/nightstatus`, and the morning brief's existing attention-required section.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.dispatch.approvals import ApprovalService
from app.dispatch.errors import DispatchError, DispatchNotFoundError
from app.dispatch.models import DispatchRequest
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.nightshift.classifier import DEFERRED_UNTIL_MORNING, PROHIBITED, classify_job_type
from app.nightshift.models import NightQueueJob
from app.nightshift.repository import NightShiftRepository
from app.nightshift.service import NightShiftError, NightShiftService

LOGGER = logging.getLogger(__name__)

# Explicit, deterministic job_type -> (agent_id, capability) routing table.
# Every REGISTERED_JOBS entry that can legitimately reach dispatch (i.e. is
# not PROHIBITED) must have exactly one entry here. A job_type absent from
# this table fails closed in `_resolve_route` rather than falling back to
# any agent. Capabilities are validated against the real AgentRegistry
# before every dispatch.
_JOB_TYPE_ROUTES: dict[str, tuple[str, str]] = {
    "execution_status_check": ("night_shift_agent", "read_only"),
    "draft_summary_prepare": ("night_shift_agent", "draft_only"),
    "dissertation_review_prepare": ("night_shift_agent", "draft_only"),
    "coding_task_prepare": ("coding_agent", "draft_only"),
    "architecture_review_prepare": ("architecture_agent", "draft_only"),
    "generic_ai_task_prepare": ("generic_ai_agent", "draft_only"),
}

_TERMINAL_JOB_STATUSES = frozenset({"completed", "rejected", "failed_safely"})


class NightJobAlreadyClaimedError(Exception):
    """Raised when claiming a job that is already leased or not claimable."""


class ProhibitedNightJobError(Exception):
    """Raised when attempting to execute a prohibited job."""


class UnknownJobRouteError(Exception):
    """Raised when a job type has no explicit agent/capability route."""


class NightJobTerminalError(Exception):
    """Raised when an operation is attempted against an already-terminal job."""


def _safe_log(operation: str, job: NightQueueJob | None, category: str) -> None:
    """Log an operation outcome using only pre-sanitized, bounded fields.

    Never pass an exception object, its message, `repr()`, or a traceback
    here — `job_id`/`category` are the only dynamic values, and both are
    short, caller-controlled identifiers, never raw exception text.
    """
    LOGGER.error(
        "night_shift_worker event=%s job_id=%s category=%s",
        operation,
        job.job_id if job is not None else "-",
        category,
    )


class NightShiftWorker:
    def __init__(
        self,
        service: NightShiftService,
        dispatch_service: DispatchService,
        approval_service: ApprovalService,
        agent_registry: AgentRegistry,
        worker_id: str | None = None,
        clock: Any | None = None,
        max_concurrency: int = 3,
        lease_duration_seconds: int = 300,
        max_attempts: int = 3,
        backoff_seconds: int = 300,
    ) -> None:
        self.service = service
        self.repository: NightShiftRepository = service.repository
        self.dispatch_service = dispatch_service
        self.approval_service = approval_service
        self.agent_registry = agent_registry
        self.worker_id = worker_id or str(uuid.uuid4())
        self.clock = clock
        self.max_concurrency = max_concurrency
        self.lease_duration_seconds = lease_duration_seconds
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds

    # -- clock seam --------------------------------------------------------

    def _utc_now_dt(self) -> datetime:
        if self.clock is not None and hasattr(self.clock, "now"):
            return self.clock.now(UTC).replace(microsecond=0)
        return datetime.now(UTC).replace(microsecond=0)

    def _utc_now(self) -> str:
        return self._utc_now_dt().isoformat().replace("+00:00", "Z")

    def _actor(self) -> str:
        return f"system:night_shift_worker:{self.worker_id}"

    # -- routing -------------------------------------------------------------

    def _resolve_route(self, job_type: str) -> tuple[str, str]:
        route = _JOB_TYPE_ROUTES.get(job_type)
        if route is None:
            raise UnknownJobRouteError(f"no route registered for job type")
        agent_id, capability = route
        self.agent_registry.validate_capability(agent_id, capability)
        return agent_id, capability

    # -- eligibility -----------------------------------------------------------

    def list_eligible_jobs(self, limit: int | None = None) -> list[NightQueueJob]:
        """Returns jobs ready for execution, adhering to runtime mode and rules."""
        mode = self.repository.get_mode()
        if not mode or mode.mode != "night_shift":
            return []

        now = self._utc_now_dt()
        actor = self._actor()
        eligible: list[NightQueueJob] = []
        # 'preparing' jobs are jobs already claimed at least once (e.g. a
        # retryable failure released the lease without changing status) —
        # they remain re-claimable once their lease is free and eligible_after
        # has passed, without ever needing a transition back to 'queued'
        # (which does not exist in the unmodified 5A.1 JOB_TRANSITIONS).
        for status in ("queued", "preparing"):
            for job in self.repository.list_jobs(status=status):
                if limit and len(eligible) >= limit:
                    return eligible

                classification = classify_job_type(job.job_type)
                if classification is None:
                    continue
                if classification.disposition == PROHIBITED:
                    # A legitimately-enqueued job can never be PROHIBITED
                    # (NightShiftService.enqueue_night_job rejects those before
                    # insertion) — this guards a row that reached the queue by
                    # some other path (e.g. a reclassification) and ensures it
                    # never sits invisible forever.
                    self._reject_prohibited(job, actor)
                    continue

                if job.eligible_after:
                    try:
                        eligible_time = datetime.fromisoformat(job.eligible_after.replace("Z", "+00:00"))
                        if now < eligible_time:
                            continue
                    except ValueError:
                        pass

                if job.attempt_count >= self.max_attempts:
                    continue

                if job.lease_expires_at:
                    try:
                        expiry_time = datetime.fromisoformat(job.lease_expires_at.replace("Z", "+00:00"))
                        if now < expiry_time:
                            continue
                    except ValueError:
                        pass

                eligible.append(job)

        return eligible

    def _reject_prohibited(self, job: NightQueueJob, actor: str) -> None:
        try:
            self.service.transition_night_job(job.id, "rejected", actor)
        except NightShiftError:
            return
        self.service.notify(
            "night_shift_job_rejected_prohibited",
            "attention_required",
            summary="A prohibited job type was found queued and was rejected automatically.",
            related_job_id=job.id,
        )

    # -- claim -----------------------------------------------------------------

    def claim_job(self, job: NightQueueJob) -> NightQueueJob:
        """Atomically acquire a lease, then legally advance queued->preparing."""
        now_dt = self._utc_now_dt()
        now_str = now_dt.isoformat().replace("+00:00", "Z")
        expires_at = (now_dt + timedelta(seconds=self.lease_duration_seconds)).isoformat().replace("+00:00", "Z")

        leased = self.repository.claim_lease(job.id, self.worker_id, expires_at, now_str)
        if leased is None:
            current = self.repository.get_job(job.id)
            status = current.status if current is not None else "unknown"
            raise NightJobAlreadyClaimedError(f"job is not claimable (status={status})")

        if leased.status == "queued":
            try:
                self.service.transition_night_job(leased.id, "preparing", self.worker_id)
            except NightShiftError:
                # Should not happen (we hold the exclusive lease), but fail
                # safe rather than leave the job stuck mid-claim.
                self.repository.release_lease(leased.id, self._utc_now(), self.worker_id, "job_claim_transition_failed")
                raise NightJobAlreadyClaimedError("job changed state during claim") from None

        return self.repository.get_job(job.id)

    # -- dispatch ----------------------------------------------------------

    def execute_via_dispatch(self, job: NightQueueJob) -> None:
        """Safely executes the job via the real DispatchService."""
        classification = classify_job_type(job.job_type)
        if classification and classification.disposition == PROHIBITED:
            raise ProhibitedNightJobError(f"job type is prohibited")

        if not classification:
            self._fail_terminally(job, "invalid_classification")
            return

        if job.approval_required or classification.disposition == DEFERRED_UNTIL_MORNING:
            self.defer_for_approval(job)
            return

        actor = self._actor()
        correlation_id = self._root_correlation_id(job)
        try:
            agent_id, capability = self._resolve_route(job.job_type)
        except UnknownJobRouteError:
            self._fail_terminally(job, "unknown_route")
            return
        except DispatchError as error:
            self._fail_terminally(job, type(error).__name__)
            return

        request = DispatchRequest(
            source_type="night_shift_job",
            source_id=job.job_id,
            agent_id=agent_id,
            capability=capability,
            payload_ref="",
            idempotency_key=self._idempotency_key(job),
            requested_by=actor,
            correlation_id=correlation_id,
            max_attempts=1,
        )

        try:
            dispatch = self.dispatch_service.create_dispatch(request, actor=actor)
            self.repository.set_dispatch_id(job.id, dispatch.dispatch_id)
            result = self.dispatch_service.dispatch(dispatch.dispatch_id, actor=actor)
        except DispatchError as error:
            self._handle_dispatch_error(job, error)
            return
        except Exception:
            _safe_log("execute_via_dispatch", job, "internal_error")
            self._fail_terminally(job, "internal_error")
            return

        if result.status == "succeeded":
            self._advance_to_draft_saved(job, actor)
        elif result.status in {"failed", "timed_out"}:
            self._retry_or_fail(job, retryable=True, failure_category=f"execution_{result.status}")
        elif result.status == "cancelled":
            self._fail_terminally(job, "execution_cancelled")
        else:
            # No adapter shipped today leaves a synchronous dispatch() call in
            # an intermediate status, but fail closed rather than silently do
            # nothing if a future adapter ever does.
            self._retry_or_fail(job, retryable=True, failure_category=f"unexpected_status_{result.status}")

    def _root_correlation_id(self, job: NightQueueJob) -> str:
        return f"night_shift_job:{job.job_id}"

    def _idempotency_key(self, job: NightQueueJob) -> str:
        return f"night_shift_job:{job.job_id}:dispatch:{job.attempt_count}"

    def _handle_dispatch_error(self, job: NightQueueJob, error: DispatchError) -> None:
        category = type(error).__name__
        retryable = bool(getattr(error, "retryable", False))
        _safe_log("execute_via_dispatch", job, category)
        if retryable:
            self._retry_or_fail(job, retryable=True, failure_category=category)
        else:
            self._fail_terminally(job, category)

    # -- outcome recording (all status changes route through NightShiftService) --

    def _reach_draft_saved(self, job: NightQueueJob, actor: str) -> NightQueueJob | None:
        """Legally advance a claimed job (status 'preparing' or 'validating')
        to 'draft_saved' — the only two edges JOB_TRANSITIONS defines out of
        'preparing', and the only state besides 'queued' that can reach
        'awaiting_approval'. Returns the updated job, or ``None`` if an
        illegal transition was rejected (already handled/logged by the caller
        raising it as a NightShiftError)."""
        current = self.repository.get_job(job.id)
        status = current.status if current is not None else job.status
        if status == "preparing":
            current = self.service.transition_night_job(job.id, "validating", actor)
            status = current.status
        if status == "validating":
            current = self.service.transition_night_job(job.id, "draft_saved", actor)
        return current

    def _advance_to_draft_saved(self, job: NightQueueJob, actor: str) -> None:
        """Advance an approval-free, successfully-dispatched job as far as the
        legal 5A.1 state machine allows without faking human approval.

        See the module docstring's "Known state-model limitation" note:
        `draft_saved` is the terminal resting state for this case, not
        `completed`, because `completed` is only legally reachable from
        `awaiting_approval`.
        """
        try:
            self._reach_draft_saved(job, actor)
        except NightShiftError:
            _safe_log("_advance_to_draft_saved", job, "invalid_transition")
            self._fail_terminally(job, "invalid_transition")
            return
        self.repository.release_lease(job.id, self._utc_now(), actor, "job_dispatch_succeeded", "automated dispatch succeeded")
        self.service.notify(
            "night_shift_job_dispatch_succeeded",
            "attention_required",
            summary="Automated overnight job completed successfully (draft ready).",
            related_job_id=job.id,
        )

    def _retry_or_fail(self, job: NightQueueJob, *, retryable: bool, failure_category: str) -> None:
        current = self.repository.get_job(job.id)
        attempt_count = (current.attempt_count if current is not None else job.attempt_count) + 1
        if retryable and attempt_count < self.max_attempts:
            eligible_after = (self._utc_now_dt() + timedelta(seconds=self.backoff_seconds)).isoformat().replace("+00:00", "Z")
            self.repository.schedule_retry(job.id, attempt_count, eligible_after, failure_category, self._utc_now(), self._actor())
            self.service.notify(
                "night_shift_job_retry_scheduled",
                "attention_required",
                summary="Overnight job failed and was scheduled for retry.",
                related_job_id=job.id,
            )
            return
        exhausted = retryable and attempt_count >= self.max_attempts
        self._fail_terminally(job, failure_category, attempt_count=attempt_count, exhausted=exhausted)

    def _fail_terminally(
        self,
        job: NightQueueJob,
        failure_category: str,
        attempt_count: int | None = None,
        exhausted: bool = False,
    ) -> None:
        actor = self._actor()
        current = self.repository.get_job(job.id)
        status = current.status if current is not None else job.status
        final_attempt_count = attempt_count if attempt_count is not None else (current.attempt_count if current is not None else job.attempt_count)
        if status not in _TERMINAL_JOB_STATUSES:
            try:
                self.service.transition_night_job(job.id, "failed_safely", actor)
            except NightShiftError:
                _safe_log("_fail_terminally", job, "invalid_transition")
        # NightShiftService.transition_night_job() always stamps
        # failure_category="safe_failure" for a failed_safely transition
        # (the domain layer's own generic marker); overwrite it here with the
        # real, sanitized category so it is preserved on the job row, not
        # only in the audit-log detail text.
        self.repository.record_final_attempt(job.id, final_attempt_count, failure_category)
        self.repository.release_lease(job.id, self._utc_now(), actor, "job_failed_safely", failure_category)
        if exhausted:
            self.service.notify(
                "essential_service_repeated_failure",
                "critical",
                summary="Overnight job exhausted its retry budget and failed safely.",
                related_job_id=job.id,
            )
        else:
            self.service.notify(
                "night_shift_job_failed_safely",
                "attention_required",
                summary="Overnight job failed safely and will not be retried automatically.",
                related_job_id=job.id,
            )

    # -- approval deferral ---------------------------------------------------

    def defer_for_approval(self, job: NightQueueJob) -> None:
        """Defer a job awaiting an explicit human decision via the canonical ApprovalService."""
        actor = self._actor()
        try:
            dispatch_id = job.dispatch_id
            if not dispatch_id:
                agent_id, capability = self._resolve_route(job.job_type)
                request = DispatchRequest(
                    source_type="night_shift_job",
                    source_id=job.job_id,
                    agent_id=agent_id,
                    capability=capability,
                    payload_ref="",
                    idempotency_key=self._idempotency_key(job),
                    requested_by=actor,
                    correlation_id=self._root_correlation_id(job),
                    max_attempts=1,
                )
                dispatch = self.dispatch_service.create_dispatch(request, actor=actor)
                dispatch_id = dispatch.dispatch_id
                self.repository.set_dispatch_id(job.id, dispatch_id)

            approval = self.approval_service.request_approval(
                dispatch_id=dispatch_id,
                requested_action=f"Approve Night Shift job: {job.job_type}",
                actor=actor,
                correlation_id=self._root_correlation_id(job),
            )
            self.repository.set_approval_id(job.id, approval.approval_id)
        except (DispatchError, UnknownJobRouteError) as error:
            category = type(error).__name__
            _safe_log("defer_for_approval", job, category)
            self._fail_terminally(job, category)
            return
        except Exception:
            _safe_log("defer_for_approval", job, "internal_error")
            self._fail_terminally(job, "internal_error")
            return

        try:
            # 'awaiting_approval' is only legally reachable from 'queued' or
            # 'draft_saved' — a job claimed en route to execution is already
            # in 'preparing', so it must first legally advance to
            # 'draft_saved' (exactly the state 5A.1 defines as "the draft is
            # ready and about to become awaiting_approval on its own terms").
            current = self.repository.get_job(job.id)
            status = current.status if current is not None else job.status
            if status in {"preparing", "validating"}:
                self._reach_draft_saved(job, actor)
            self.service.transition_night_job(job.id, "awaiting_approval", actor)
        except NightShiftError:
            _safe_log("defer_for_approval", job, "invalid_transition")
            self._fail_terminally(job, "invalid_transition")
            return
        self.repository.release_lease(job.id, self._utc_now(), actor, "job_deferred", f"approval={approval.approval_id}")

    # -- cancellation --------------------------------------------------------

    def cancel_job(self, job: NightQueueJob, reason: str, actor: str) -> NightQueueJob:
        """Cancel a job in any non-terminal state.

        Folds into `failed_safely`, matching 5A.1's existing precedent —
        there is no separate `cancelled` job status. Cancels the linked
        dispatch (and, transitively, its open approval) through the
        canonical `DispatchService.cancel_dispatch`.
        """
        current = self.repository.get_job(job.id)
        if current is None:
            raise NightJobTerminalError("job not found")
        if current.status in _TERMINAL_JOB_STATUSES:
            raise NightJobTerminalError(f"job is already terminal (status={current.status})")

        if current.dispatch_id:
            try:
                self.dispatch_service.cancel_dispatch(current.dispatch_id, actor=actor, reason=reason)
            except DispatchError:
                pass  # dispatch may already be terminal on its own side; still fold the job.

        try:
            self.service.transition_night_job(current.id, "failed_safely", actor)
        except NightShiftError as error:
            raise NightJobTerminalError("job could not be cancelled") from error

        self.repository.release_lease(current.id, self._utc_now(), actor, "job_cancelled", reason)
        self.service.notify(
            "night_shift_job_cancelled",
            "attention_required",
            summary="Overnight job was cancelled by an operator.",
            related_job_id=current.id,
        )
        return self.repository.get_job(current.id)

    # -- stale-lease recovery -------------------------------------------------

    def recover_stale_job(self, job: NightQueueJob) -> None:
        """Recover a job whose lease has expired.

        Inspects the *real* linked dispatch status before deciding an
        outcome — a stale lease never means "cancel and retry" unconditionally;
        a dispatch that actually succeeded or is still awaiting approval must
        never be silently retried or discarded.
        """
        if not job.lease_expires_at:
            return
        try:
            expiry = datetime.fromisoformat(job.lease_expires_at.replace("Z", "+00:00"))
        except ValueError:
            return
        if self._utc_now_dt() <= expiry:
            return  # active lease — untouched.

        actor = self._actor()
        if not job.dispatch_id:
            self._retry_or_fail(job, retryable=True, failure_category="stale_lease_no_dispatch")
            return

        try:
            record = self.dispatch_service.get_dispatch(job.dispatch_id)
        except DispatchNotFoundError:
            self._retry_or_fail(job, retryable=True, failure_category="stale_lease_dispatch_missing")
            return

        status = record.status
        if status == "succeeded":
            self._advance_to_draft_saved(job, actor)
        elif status in {"failed", "timed_out"}:
            self._retry_or_fail(job, retryable=True, failure_category=f"stale_lease_{status}")
        elif status in {"cancelled", "rejected"}:
            self._fail_terminally(job, f"stale_lease_{status}")
        elif status == "awaiting_approval":
            # Legitimately still waiting on a human decision — release the
            # lease (it should not occupy a concurrency slot) but do not fail
            # or retry it; a future recovery pass or /approve resolves it.
            self.repository.release_lease(job.id, self._utc_now(), actor, "job_recovery_awaiting_approval", "dispatch still awaiting approval")
        elif status in {"dispatching", "running"}:
            try:
                self.dispatch_service.cancel_dispatch(job.dispatch_id, actor=actor, reason="stale lease recovery")
            except DispatchError:
                pass
            self._retry_or_fail(job, retryable=True, failure_category="stale_lease_in_flight")
        else:
            self._retry_or_fail(job, retryable=True, failure_category="stale_lease_unknown_status")
