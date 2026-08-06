from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.memory import MemoryDatabase
from app.nightshift.repository import utc_now
from app.nightshift.service import (
    DuplicateNightJobError,
    InvalidNightJobTransitionError,
    InvalidRuntimeModeError,
    InvalidScheduleError,
    NightShiftService,
    ProhibitedNightJobError,
    UnregisteredJobTypeError,
)


def service(tmp_path: Path) -> NightShiftService:
    result = NightShiftService(MemoryDatabase(tmp_path / "nova.db"))
    result.initialize()
    return result


def test_modes_persist_and_manual_override_is_sticky(tmp_path: Path) -> None:
    runtime = service(tmp_path)
    runtime.set_runtime_mode("quiet", "user:1", "sleep")
    runtime.tick(datetime(2026, 8, 6, 23, 0, tzinfo=ZoneInfo("Asia/Jakarta")))
    assert runtime.get_runtime_mode().mode == "quiet"
    restarted = NightShiftService(MemoryDatabase(tmp_path / "nova.db"))
    restarted.initialize()
    assert restarted.get_runtime_mode().mode == "quiet"
    with pytest.raises(InvalidRuntimeModeError):
        runtime.set_runtime_mode("unknown", "user:1", "bad")


def test_scheduler_mode_transitions_and_wake_boundary(tmp_path: Path) -> None:
    runtime = service(tmp_path)
    runtime.tick(datetime(2026, 8, 6, 15, 0, tzinfo=ZoneInfo("UTC")))
    assert runtime.get_runtime_mode().mode == "night_shift"
    assert runtime.wake_now("user:1").mode == "active"
    # Regression: waking mid-window must be sticky against the very next
    # tick() while still inside the night-shift window (15:00 UTC and
    # 15:30 UTC are both inside the default 22:00-06:00 Asia/Jakarta
    # window) — a prior bug computed is_manual_override as mode != "active",
    # which silently reverted a manual wake back to night_shift.
    runtime.tick(datetime(2026, 8, 6, 15, 30, tzinfo=ZoneInfo("UTC")))
    assert runtime.get_runtime_mode().mode == "active"
    with pytest.raises(InvalidRuntimeModeError): runtime.wake_now("user:1")
    runtime.tick(datetime(2026, 8, 6, 23, 0, tzinfo=ZoneInfo("UTC")))
    assert runtime.get_runtime_mode().mode == "active"


def test_manual_active_override_survives_tick_inside_window(tmp_path: Path) -> None:
    runtime = service(tmp_path)
    runtime.tick(datetime(2026, 8, 6, 15, 0, tzinfo=ZoneInfo("UTC")))
    assert runtime.get_runtime_mode().mode == "night_shift"
    # A manual set_runtime_mode("active", ...) — not just wake_now() — must
    # also be sticky against the scheduler.
    runtime.set_runtime_mode("active", "user:1", "working late")
    runtime.tick(datetime(2026, 8, 6, 16, 0, tzinfo=ZoneInfo("UTC")))
    assert runtime.get_runtime_mode().mode == "active"


def test_stale_automatic_transition_cannot_clobber_newer_manual_state(tmp_path: Path) -> None:
    runtime = service(tmp_path)
    runtime.tick(datetime(2026, 8, 6, 15, 0, tzinfo=ZoneInfo("UTC")))
    stale_state = runtime.get_runtime_mode()
    assert stale_state.mode == "night_shift"
    # A manual override races in and wins.
    runtime.set_runtime_mode("quiet", "user:1", "do not disturb")
    # A stale automatic writer that read the mode before the manual override
    # must not silently clobber the newer manual state.
    result = runtime.repository.set_mode(
        "active", False, "system:scheduler", "stale scheduled boundary",
        utc_now(), expected_mode=stale_state.mode,
    )
    assert result is None
    assert runtime.get_runtime_mode().mode == "quiet"


def test_schedule_validation_and_midnight_window(tmp_path: Path) -> None:
    runtime = service(tmp_path)
    runtime.update_night_schedule("22:00", "06:00", "07:00", "Asia/Jakarta", "user:1")
    schedule = runtime.get_night_schedule()
    assert runtime.is_night_shift_window(datetime(2026, 8, 6, 15, 0, tzinfo=ZoneInfo("UTC")), schedule)
    assert runtime.is_night_shift_window(datetime(2026, 8, 6, 20, 59, tzinfo=ZoneInfo("UTC")), schedule)
    assert not runtime.is_night_shift_window(datetime(2026, 8, 6, 23, 0, tzinfo=ZoneInfo("UTC")), schedule)
    with pytest.raises(InvalidScheduleError): runtime.update_night_schedule("25:00", "06:00", "07:00")
    with pytest.raises(InvalidScheduleError): runtime.update_night_schedule("22:00", "06:00", "07:00", "No/Such_Zone")


def test_queue_allowlist_deduplication_and_transitions(tmp_path: Path) -> None:
    runtime = service(tmp_path)
    job = runtime.enqueue_night_job("draft_summary_prepare", "same", "job-1")
    with pytest.raises(DuplicateNightJobError): runtime.enqueue_night_job("draft_summary_prepare", "same", "job-2")
    with pytest.raises(UnregisteredJobTypeError): runtime.enqueue_night_job("unknown", "new", "job-3")
    with pytest.raises(ProhibitedNightJobError): runtime.enqueue_night_job("git_commit", "new", "job-4")
    assert runtime.transition_night_job(job.id, "preparing").status == "preparing"
    with pytest.raises(InvalidNightJobTransitionError): runtime.transition_night_job(job.id, "completed")
    assert runtime.transition_night_job(job.id, "validating").status == "validating"
    assert runtime.transition_night_job(job.id, "draft_saved").status == "draft_saved"
    assert runtime.transition_night_job(job.id, "awaiting_approval").status == "awaiting_approval"


def test_maintenance_rejects_intake_and_queue_order_is_deterministic(tmp_path: Path) -> None:
    runtime = service(tmp_path)
    runtime.enqueue_night_job("execution_status_check", "b", "job-b", eligible_after="2026-08-07T02:00:00Z")
    runtime.enqueue_night_job("execution_status_check", "a", "job-a", eligible_after="2026-08-07T01:00:00Z")
    assert [job.job_id for job in runtime.list_night_jobs()] == ["job-a", "job-b"]
    runtime.set_runtime_mode("maintenance", "user:1", "upgrade")
    with pytest.raises(Exception, match="Maintenance"):
        runtime.enqueue_night_job("execution_status_check", "c", "job-c")
