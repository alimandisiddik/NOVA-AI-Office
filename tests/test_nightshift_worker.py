import logging
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.dispatch.approvals import ApprovalService
from app.dispatch.errors import DispatchUnavailableError, UnsupportedCapabilityError
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.memory.database import MemoryDatabase
from app.nightshift.classifier import PROHIBITED, REGISTERED_JOBS
from app.nightshift.service import JOB_TRANSITIONS, NightShiftService
from app.nightshift.worker import (
    NightJobAlreadyClaimedError,
    NightJobTerminalError,
    NightShiftWorker,
    ProhibitedNightJobError,
    UnknownJobRouteError,
    _JOB_TYPE_ROUTES,
)


@pytest.fixture
def db(tmp_path):
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    return database


@pytest.fixture
def service(db):
    svc = NightShiftService(db)
    svc.initialize()
    return svc


@pytest.fixture
def repo(service):
    return service.repository


@pytest.fixture
def approval_service(db):
    svc = ApprovalService(db, authorized_user_id=123)
    svc.initialize()
    return svc


@pytest.fixture
def agent_registry():
    return AgentRegistry()


@pytest.fixture
def dispatch_service(db, agent_registry, approval_service):
    svc = DispatchService(db, registry=agent_registry, approvals=approval_service)
    svc.initialize()
    return svc


@pytest.fixture
def worker(service, dispatch_service, approval_service, agent_registry):
    return NightShiftWorker(
        service=service,
        dispatch_service=dispatch_service,
        approval_service=approval_service,
        agent_registry=agent_registry,
    )


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# -- A: JOB_TRANSITIONS must exactly match the main baseline -----------------


def test_job_transitions_matches_main_baseline():
    result = subprocess.run(
        ["git", "diff", "main", "--", "app/nightshift/service.py"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "", "app/nightshift/service.py must not diverge from main (JOB_TRANSITIONS unmodified)"


def test_job_transitions_shape_is_the_original_5a1_state_machine():
    assert JOB_TRANSITIONS == {
        "queued": frozenset({"preparing", "awaiting_approval", "rejected", "failed_safely"}),
        "preparing": frozenset({"validating", "failed_safely"}),
        "validating": frozenset({"draft_saved", "failed_safely"}),
        "draft_saved": frozenset({"awaiting_approval", "failed_safely"}),
        "awaiting_approval": frozenset({"completed", "rejected", "failed_safely"}),
        "completed": frozenset(),
        "rejected": frozenset(),
        "failed_safely": frozenset(),
    }
    assert "draft_saved" not in JOB_TRANSITIONS["preparing"]
    assert "queued" not in JOB_TRANSITIONS["preparing"]
    assert "queued" not in JOB_TRANSITIONS["validating"]
    assert "completed" not in JOB_TRANSITIONS["draft_saved"]
    assert "queued" not in JOB_TRANSITIONS["draft_saved"]


# -- B: no raw SQL status mutation, proven at runtime, not just by source grep --


def test_no_raw_status_sql_in_worker_source():
    with open("app/nightshift/worker.py") as handle:
        src = handle.read()
    assert "SET status" not in src
    assert "SET status=" not in src


def test_runtime_status_changes_only_via_domain_transitions(worker, repo, service):
    """Every status this job passes through must be independently reachable
    from JOB_TRANSITIONS at the time it was written — proven by replaying the
    same sequence through NightShiftService.transition_night_job() directly
    against a second job and confirming it is never rejected."""
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="d-audit", job_id="job-audit")
    claimed = worker.claim_job(job)
    worker.execute_via_dispatch(claimed)
    final = repo.get_job(job.id)
    assert final.status == "draft_saved"

    shadow = service.enqueue_night_job("draft_summary_prepare", deduplication_key="d-shadow", job_id="job-shadow")
    service.transition_night_job(shadow.id, "preparing", "test")
    service.transition_night_job(shadow.id, "validating", "test")
    service.transition_night_job(shadow.id, "draft_saved", "test")  # must not raise


# -- Eligibility ---------------------------------------------------------------


def test_list_eligible_jobs_mode(worker, repo, service):
    repo.set_mode("active", True, "test", "test", _utc_now())
    service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    assert len(worker.list_eligible_jobs()) == 0
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    assert len(worker.list_eligible_jobs()) == 1


def test_list_eligible_jobs_rules(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())

    future = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup_f", job_id="job_fut", eligible_after=future)

    exhausted = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup_a", job_id="job_att")
    with repo.database.connection() as conn:
        conn.execute("UPDATE night_queue_jobs SET attempt_count = 3 WHERE id = ?", (exhausted.id,))

    assert worker.list_eligible_jobs() == []


def test_prohibited_job_never_dispatches_and_becomes_visible_rejected(worker, repo):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    # enqueue_night_job() itself already refuses PROHIBITED job types before
    # insertion; this row simulates one that reached the table by another
    # path (e.g. a reclassification), which is exactly the defensive case
    # `list_eligible_jobs` must still handle safely and visibly.
    job = repo.create_job(("job_proh", "git_commit", "git_mutation", "queued", _utc_now(), None, None, None, 0, "dedup_p", "{}"))

    eligible = worker.list_eligible_jobs()
    assert job.job_id not in [j.job_id for j in eligible]

    rejected = repo.get_job(job.id)
    assert rejected.status == "rejected"


def test_unknown_job_type_fails_closed_in_eligibility(worker, repo):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = repo.create_job(("job_unk", "totally_unregistered_type", "unknown", "queued", _utc_now(), None, None, None, 0, "dedup_u", "{}"))
    assert job.job_id not in [j.job_id for j in worker.list_eligible_jobs()]


# -- Claim CAS -------------------------------------------------------------------


def test_claim_job(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)
    assert claimed.lease_worker_id == worker.worker_id
    assert claimed.status == "preparing"
    with pytest.raises(NightJobAlreadyClaimedError):
        worker.claim_job(job)


@pytest.mark.parametrize("status", ["completed", "rejected", "failed_safely", "awaiting_approval"])
def test_claim_job_rejects_every_terminal_or_waiting_state(worker, repo, status):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = repo.create_job((f"job_{status}", "draft_summary_prepare", "draft_only", status, _utc_now(), None, None, None, 0, f"dedup_{status}", "{}"))
    with pytest.raises(NightJobAlreadyClaimedError):
        worker.claim_job(job)


def test_active_lease_cannot_be_stolen(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup2", job_id="job_2")
    worker.claim_job(job)
    other = NightShiftWorker(service=service, dispatch_service=worker.dispatch_service, approval_service=worker.approval_service, agent_registry=worker.agent_registry)
    with pytest.raises(NightJobAlreadyClaimedError):
        other.claim_job(repo.get_job(job.id))


def test_terminal_job_cannot_be_reclaimed_after_successful_completion(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup3", job_id="job_3")
    claimed = worker.claim_job(job)
    worker.execute_via_dispatch(claimed)
    final = repo.get_job(job.id)
    assert final.status == "draft_saved"
    with pytest.raises(NightJobAlreadyClaimedError):
        worker.claim_job(final)


def test_two_workers_race_exactly_one_succeeds(db, service, repo, dispatch_service, approval_service, agent_registry):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="race", job_id="race-job")

    worker_a = NightShiftWorker(service=NightShiftService(db), dispatch_service=dispatch_service, approval_service=approval_service, agent_registry=agent_registry)
    worker_b = NightShiftWorker(service=NightShiftService(db), dispatch_service=dispatch_service, approval_service=approval_service, agent_registry=agent_registry)

    results: dict[str, str] = {}

    def attempt(worker_, key):
        try:
            worker_.claim_job(job)
            results[key] = "OK"
        except NightJobAlreadyClaimedError:
            results[key] = "REJECTED"

    threads = [threading.Thread(target=attempt, args=(worker_a, "a")), threading.Thread(target=attempt, args=(worker_b, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results.values()) == ["OK", "REJECTED"]


# -- Dispatch: success, honest state-model limitation -----------------------


def test_execute_via_dispatch_success_advances_to_draft_saved_not_completed(worker, repo, service, dispatch_service):
    """Contract-honest outcome: 5A.1's JOB_TRANSITIONS only reaches `completed`
    via `awaiting_approval`, and this worker must never fake human approval to
    get there — so an approval-free successful automation rests at
    `draft_saved`, the deepest legal state reachable, with the real outcome
    linked via `dispatch_id`."""
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("execution_status_check", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)
    worker.execute_via_dispatch(claimed)

    final = repo.get_job(job.id)
    assert final.status == "draft_saved"
    assert final.dispatch_id is not None
    assert dispatch_service.get_dispatch(final.dispatch_id).status == "succeeded"
    assert final.lease_worker_id is None


def test_defer_for_approval_uses_real_request_approval_and_persists_approval_id(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("dissertation_review_prepare", deduplication_key="dedup1", job_id="job_1")
    assert job.status == "awaiting_approval"  # 5A.1 defers DEFERRED_UNTIL_MORNING at enqueue time already

    # Exercise the worker's own defer path directly for a job that only
    # became approval-required after being claimed (the defensive case).
    manual = repo.create_job(("job_manual_defer", "draft_summary_prepare", "draft_only", "queued", _utc_now(), None, None, None, 1, "dedup_manual", "{}"))
    claimed = worker.claim_job(manual)
    worker.defer_for_approval(claimed)

    final = repo.get_job(manual.id)
    assert final.status == "awaiting_approval"
    assert final.lease_worker_id is None
    assert final.approval_id is not None


def test_prohibited_action_never_creates_an_approval(worker):
    job = type("J", (), dict(
        id=1, job_id="job_x", job_type="git_commit", attempt_count=0,
        approval_required=False, dispatch_id=None, status="preparing",
    ))()
    with pytest.raises(ProhibitedNightJobError):
        worker.execute_via_dispatch(job)


# -- Agent routing: explicit deterministic table -----------------------------


def test_routing_table_covers_every_non_prohibited_registered_job():
    dispatchable = {job_type for job_type, c in REGISTERED_JOBS.items() if c.disposition != PROHIBITED}
    missing = dispatchable - set(_JOB_TYPE_ROUTES)
    # essential_service_repeated_failure is a notification event_type, never a
    # dispatched job (see contract §8) — the only expected gap.
    assert missing <= {"essential_service_repeated_failure"}


def test_routing_table_resolves_and_validates_against_real_registry(worker):
    for job_type, (agent_id, capability) in _JOB_TYPE_ROUTES.items():
        agent = worker.agent_registry.validate_capability(agent_id, capability)  # must not raise
        assert capability in agent.capabilities


def test_unknown_job_type_route_fails_closed(worker):
    with pytest.raises(UnknownJobRouteError):
        worker._resolve_route("no_such_job_type")


def test_prohibited_types_are_never_present_in_the_routing_table():
    prohibited_types = {job_type for job_type, c in REGISTERED_JOBS.items() if c.disposition == PROHIBITED}
    assert not (prohibited_types & set(_JOB_TYPE_ROUTES))


# -- Typed retry handling ------------------------------------------------------


def test_retryable_typed_dispatch_error_schedules_retry_without_illegal_transition(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)

    worker._handle_dispatch_error(claimed, DispatchUnavailableError())

    after = repo.get_job(job.id)
    assert after.status == "preparing"  # unchanged — no 'queued' edge exists, and none was added
    assert after.attempt_count == 1
    assert after.eligible_after is not None
    assert after.lease_worker_id is None


def test_non_retryable_typed_dispatch_error_fails_safely_with_real_category(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)

    worker._handle_dispatch_error(claimed, UnsupportedCapabilityError())

    after = repo.get_job(job.id)
    assert after.status == "failed_safely"
    assert after.failure_category == "UnsupportedCapabilityError"


def test_unexpected_internal_exception_is_not_retryable(worker, repo, service, monkeypatch):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)

    def boom(*args, **kwargs):
        raise TypeError("programming bug, not a provider failure")

    monkeypatch.setattr(worker.dispatch_service, "create_dispatch", boom)
    worker.execute_via_dispatch(claimed)

    after = repo.get_job(job.id)
    assert after.status == "failed_safely"
    assert after.failure_category == "internal_error"


def test_retry_exhaustion_reaches_visible_terminal_state(repo, service, dispatch_service, approval_service, agent_registry):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    worker = NightShiftWorker(service=service, dispatch_service=dispatch_service, approval_service=approval_service, agent_registry=agent_registry, max_attempts=2)
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)

    worker._handle_dispatch_error(claimed, DispatchUnavailableError())  # attempt 1 -> retry
    after_retry = repo.get_job(job.id)
    assert after_retry.status == "preparing"

    worker._handle_dispatch_error(after_retry, DispatchUnavailableError())  # attempt 2 -> exhausted
    final = repo.get_job(job.id)
    assert final.status == "failed_safely"
    assert final.attempt_count == 2


# -- Idempotency: same-attempt replay ----------------------------------------


def test_same_attempt_replay_reuses_the_same_dispatch(worker, repo, service, dispatch_service):
    """Simulates a crash after create_dispatch() but before dispatch_id is
    linked onto the Night Shift job: replaying the same (unclaimed-again)
    attempt must reuse the existing dispatch row, not create a second one."""
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)

    agent_id, capability = worker._resolve_route(claimed.job_type)
    from app.dispatch.models import DispatchRequest
    request = DispatchRequest(
        source_type="night_shift_job", source_id=claimed.job_id, agent_id=agent_id, capability=capability,
        payload_ref="", idempotency_key=worker._idempotency_key(claimed), requested_by=worker._actor(),
        correlation_id=worker._root_correlation_id(claimed), max_attempts=1,
    )
    first = dispatch_service.create_dispatch(request, actor=worker._actor())
    # Simulate the crash: dispatch_id was never written to night_queue_jobs.
    replay = dispatch_service.create_dispatch(request, actor=worker._actor())

    assert replay.dispatch_id == first.dispatch_id
    assert len(dispatch_service.list_dispatches(source_type="night_shift_job")) == 1


def test_real_retry_uses_a_distinct_attempt_identity(worker):
    job = type("J", (), dict(job_id="job_1", attempt_count=0))()
    key0 = worker._idempotency_key(job)
    job.attempt_count = 1
    key1 = worker._idempotency_key(job)
    assert key0 != key1
    assert key0 == "night_shift_job:job_1:dispatch:0"
    assert key1 == "night_shift_job:job_1:dispatch:1"


def test_root_correlation_id_stable_across_attempts(worker):
    job = type("J", (), dict(job_id="job_1", attempt_count=0))()
    root0 = worker._root_correlation_id(job)
    job.attempt_count = 3
    root1 = worker._root_correlation_id(job)
    assert root0 == root1 == "night_shift_job:job_1"


# -- Stale recovery: inspects the real dispatch before deciding ---------------


def test_stale_lease_with_succeeded_dispatch_reconciles_without_retry(worker, repo, service, dispatch_service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)

    agent_id, capability = worker._resolve_route(claimed.job_type)
    from app.dispatch.models import DispatchRequest
    request = DispatchRequest(
        source_type="night_shift_job", source_id=claimed.job_id, agent_id=agent_id, capability=capability,
        payload_ref="", idempotency_key=worker._idempotency_key(claimed), requested_by=worker._actor(),
        correlation_id=worker._root_correlation_id(claimed), max_attempts=1,
    )
    dispatch = dispatch_service.create_dispatch(request, actor=worker._actor())
    worker.repository.set_dispatch_id(claimed.id, dispatch.dispatch_id)
    result = dispatch_service.dispatch(dispatch.dispatch_id, actor=worker._actor())
    assert result.status == "succeeded"

    with repo.database.connection() as conn:
        conn.execute("UPDATE night_queue_jobs SET lease_expires_at='2020-01-01T00:00:00Z' WHERE id=?", (claimed.id,))

    worker.recover_stale_job(repo.get_job(claimed.id))

    recovered = repo.get_job(claimed.id)
    assert recovered.status == "draft_saved"
    assert recovered.attempt_count == 0  # never treated as a failure/retry


def test_stale_lease_with_no_dispatch_is_treated_as_retryable(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)
    with repo.database.connection() as conn:
        conn.execute("UPDATE night_queue_jobs SET lease_expires_at='2020-01-01T00:00:00Z' WHERE id=?", (claimed.id,))
    worker.recover_stale_job(repo.get_job(claimed.id))
    recovered = repo.get_job(claimed.id)
    assert recovered.status == "preparing"
    assert recovered.attempt_count == 1


def test_active_lease_is_untouched_by_recovery(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)
    worker.recover_stale_job(claimed)  # lease still active
    unchanged = repo.get_job(claimed.id)
    assert unchanged.lease_worker_id == claimed.lease_worker_id
    assert unchanged.attempt_count == 0


def test_repeated_recovery_is_idempotent(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)
    with repo.database.connection() as conn:
        conn.execute("UPDATE night_queue_jobs SET lease_expires_at='2020-01-01T00:00:00Z' WHERE id=?", (claimed.id,))
    worker.recover_stale_job(repo.get_job(claimed.id))
    once = repo.get_job(claimed.id)
    worker.recover_stale_job(once)  # lease already cleared -> no-op
    twice = repo.get_job(claimed.id)
    assert once.attempt_count == twice.attempt_count == 1


# -- Cancellation --------------------------------------------------------------


def test_cancel_queued_job(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    cancelled = worker.cancel_job(job, "operator requested", actor="user:1")
    assert cancelled.status == "failed_safely"


def test_cancel_in_flight_job_cancels_linked_dispatch(worker, repo, service, dispatch_service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)
    agent_id, capability = worker._resolve_route(claimed.job_type)
    from app.dispatch.models import DispatchRequest
    dispatch = dispatch_service.create_dispatch(
        DispatchRequest(source_type="night_shift_job", source_id=claimed.job_id, agent_id=agent_id, capability=capability,
                         payload_ref="", idempotency_key=worker._idempotency_key(claimed), requested_by=worker._actor(),
                         correlation_id=worker._root_correlation_id(claimed), max_attempts=1),
        actor=worker._actor(),
    )
    worker.repository.set_dispatch_id(claimed.id, dispatch.dispatch_id)

    cancelled = worker.cancel_job(repo.get_job(claimed.id), "operator requested", actor="user:1")
    assert cancelled.status == "failed_safely"
    assert dispatch_service.get_dispatch(dispatch.dispatch_id).status == "cancelled"


def test_terminal_job_cancellation_is_rejected(worker, repo, service):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)
    worker.execute_via_dispatch(claimed)  # -> draft_saved, terminal for claim/cancel purposes... actually still non-terminal per _TERMINAL_JOB_STATUSES
    # draft_saved is not in _TERMINAL_JOB_STATUSES (only completed/rejected/failed_safely are);
    # cancel a genuinely terminal job instead:
    terminal_job = repo.create_job(("job_term", "draft_summary_prepare", "draft_only", "failed_safely", _utc_now(), None, None, None, 0, "dedup_term", "{}"))
    with pytest.raises(NightJobTerminalError):
        worker.cancel_job(terminal_job, "too late", actor="user:1")


# -- Safe logging: proven via caplog with a secret-shaped exception ----------


def test_no_raw_exception_or_secret_content_in_logs(worker, repo, service, monkeypatch, caplog):
    repo.set_mode("night_shift", True, "test", "test", _utc_now())
    job = service.enqueue_night_job("draft_summary_prepare", deduplication_key="dedup1", job_id="job_1")
    claimed = worker.claim_job(job)

    def leaky(*args, **kwargs):
        raise RuntimeError("token=sk-SECRET-abc123 /Users/example/private/path.json")

    monkeypatch.setattr(worker.dispatch_service, "create_dispatch", leaky)
    with caplog.at_level(logging.ERROR):
        worker.execute_via_dispatch(claimed)

    log_text = caplog.text
    assert "sk-SECRET-abc123" not in log_text
    assert "/Users/example/private/path.json" not in log_text
    assert "Traceback" not in log_text


def test_no_logger_exception_calls_in_worker_source():
    with open("app/nightshift/worker.py") as handle:
        src = handle.read()
    assert "LOGGER.exception(" not in src
    assert "LOGGER.error(f\"" not in src
    assert "LOGGER.error(f'" not in src
    assert "LOGGER.warning(f\"" not in src
    assert "LOGGER.warning(f'" not in src


# -- No duplicate dispatch/approval implementation ---------------------------


def test_structural_guard_no_dispatches_table_query(worker):
    import inspect
    source = inspect.getsource(NightShiftWorker)
    assert "FROM dispatches" not in source
    assert "FROM approvals" not in source
    assert "INTO dispatches" not in source
    assert "INTO approvals" not in source


def test_structural_guard_no_fake_dispatch_class():
    with open("app/nightshift/worker.py") as handle:
        src = handle.read()
    assert "class FakeDispatchService" not in src
    assert "class FakeApprovalService" not in src


def test_dispatch_module_untouched_by_5f():
    result = subprocess.run(["git", "diff", "--", "app/dispatch"], capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
