import inspect

import pytest

import app.telegram_bot as tb
from app.dispatch.approvals import ApprovalService
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.memory.database import MemoryDatabase
from app.nightshift.service import NightShiftService
from app.nightshift.worker import NightShiftWorker


class _FakeJob:
    def __init__(self, data):
        self.data = data


class _FakeContext:
    def __init__(self, job):
        self.job = job


@pytest.fixture
def wired_worker(tmp_path):
    db = MemoryDatabase(tmp_path / "workspace.db")
    db.initialize()
    night_shift = NightShiftService(db)
    night_shift.initialize()
    night_shift.repository.set_mode("night_shift", True, "test", "test", "2026-08-07T00:00:00Z")
    approvals = ApprovalService(db, authorized_user_id=1)
    approvals.initialize()
    registry = AgentRegistry()
    dispatch = DispatchService(db, registry=registry, approvals=approvals)
    dispatch.initialize()
    worker = NightShiftWorker(service=night_shift, dispatch_service=dispatch, approval_service=approvals, agent_registry=registry)
    return night_shift, worker


def test_scheduler_registration_source_present():
    source = inspect.getsource(tb.build_application)
    assert "application.job_queue.run_repeating(" in source
    assert "_nightshift_tick" in source
    assert source.count("run_repeating(") == 1


def test_worker_constructed_exactly_once_in_main():
    import inspect as _inspect
    from app.main import main
    source = _inspect.getsource(main)
    assert source.count("NightShiftWorker(") == 1


def test_nightshift_commands_registered_exactly_once():
    source = inspect.getsource(tb.build_application)
    for command in ("nightshift", "nightstatus", "nightqueue", "wake"):
        assert source.count(f'CommandHandler("{command}"') == 1


# -- D: the scheduler callback must actually execute against a realistic --
# -- PTB 22.8 Job/CallbackContext shape (Job.data, not Job.context). -------


def test_tick_callback_uses_job_data_not_job_context_and_runs_eligible_jobs(wired_worker):
    night_shift, worker = wired_worker
    job = night_shift.enqueue_night_job("draft_summary_prepare", deduplication_key="tick-dedup", job_id="tick-job")

    ctx = _FakeContext(_FakeJob({"night_shift": night_shift, "night_worker": worker}))
    import asyncio
    asyncio.run(tb._nightshift_tick(ctx))  # must not raise AttributeError

    after = night_shift.repository.get_job(job.id)
    assert after.status == "draft_saved"
    assert after.dispatch_id is not None


def test_tick_callback_missing_data_fails_safely():
    ctx = _FakeContext(_FakeJob({}))
    import asyncio
    asyncio.run(tb._nightshift_tick(ctx))  # must not raise


def test_tick_callback_missing_job_fails_safely():
    class NoJobContext:
        job = None

    import asyncio
    asyncio.run(tb._nightshift_tick(NoJobContext()))  # must not raise


def test_tick_callback_isolates_one_bad_job_from_the_rest(wired_worker, monkeypatch):
    night_shift, worker = wired_worker
    bad = night_shift.enqueue_night_job("draft_summary_prepare", deduplication_key="bad-dedup", job_id="bad-job")
    good = night_shift.enqueue_night_job("draft_summary_prepare", deduplication_key="good-dedup", job_id="good-job")

    original_execute = worker.execute_via_dispatch

    def flaky(job):
        if job.job_id == "bad-job":
            raise RuntimeError("boom")
        original_execute(job)

    monkeypatch.setattr(worker, "execute_via_dispatch", flaky)
    ctx = _FakeContext(_FakeJob({"night_shift": night_shift, "night_worker": worker}))
    import asyncio
    asyncio.run(tb._nightshift_tick(ctx))  # must not raise despite the bad job

    good_final = night_shift.repository.get_job(good.id)
    assert good_final.status == "draft_saved"


def test_overlapping_tick_is_skipped_not_run_concurrently(wired_worker):
    night_shift, worker = wired_worker
    ctx = _FakeContext(_FakeJob({"night_shift": night_shift, "night_worker": worker}))

    assert tb._nightshift_tick_lock.acquire(blocking=False)
    try:
        import asyncio
        asyncio.run(tb._nightshift_tick(ctx))  # should skip cleanly, not block or raise
    finally:
        tb._nightshift_tick_lock.release()

    assert tb._nightshift_tick_lock.acquire(blocking=False)
    tb._nightshift_tick_lock.release()


def test_no_job_context_access_anywhere_in_telegram_bot():
    """`Job.context` does not exist on the installed PTB version; only the
    real accessor, `context.job.data`, may appear as executable code. This
    inspects the compiled AST rather than raw text so the module's own
    explanatory docstring (which names the wrong attribute as a warning)
    cannot trigger a false positive."""
    import ast

    with open("app/telegram_bot.py") as handle:
        tree = ast.parse(handle.read())

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "context":
            assert False, "found a real .context attribute access — PTB 22.8 has no such attribute on Job"


# -- M: explicit Night Shift cancellation surface ----------------------------


def test_nightqueue_cancel_argument_parsing_present():
    source = inspect.getsource(tb.nightqueue_command)
    assert '"cancel"' in source
    assert "cancel_job" in source

def test_nightshift_tick_is_coroutine():
    import asyncio
    assert asyncio.iscoroutinefunction(tb._nightshift_tick)
