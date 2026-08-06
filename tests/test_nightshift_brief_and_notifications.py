from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.memory import MemoryDatabase
from app.nightshift.service import NightShiftError, NightShiftService


def service(tmp_path: Path) -> NightShiftService:
    result = NightShiftService(MemoryDatabase(tmp_path / "nova.db"))
    result.initialize()
    return result


def test_notification_routing_and_critical_policy(tmp_path: Path) -> None:
    runtime = service(tmp_path)
    assert runtime.record_notification_event("completed", "informational").routing == "morning_brief"
    assert runtime.record_notification_event("deferred_job", "attention_required").routing == "prioritized_morning_brief"
    assert runtime.record_notification_event("security_incident", "critical").routing == "immediate_eligible"
    assert runtime.classify_notification("test_failure") != "critical"
    assert runtime.classify_notification("provider_rate_limit") != "critical"
    assert runtime.classify_notification("security_incident") == "critical"
    with pytest.raises(NightShiftError): runtime.record_notification_event("test_failure", "critical")
    with pytest.raises(NightShiftError): runtime.record_notification_event("completed", "informational", "api_key=do-not-store")
    # Regression: classify_notification() fails closed to "critical" for any
    # unregistered event_type (by design, for its own advisory purpose), but
    # record_notification_event() must not trust that alone — a caller must
    # not be able to self-assign critical severity via a made-up event_type
    # that merely inherits the fail-closed default.
    with pytest.raises(NightShiftError):
        runtime.record_notification_event("something_not_registered", "critical")


def test_brief_is_consolidated_and_idempotent(tmp_path: Path) -> None:
    runtime = service(tmp_path)
    job = runtime.enqueue_night_job("draft_summary_prepare", "draft", "job-1")
    for status in ("preparing", "validating", "draft_saved", "awaiting_approval", "completed"):
        runtime.transition_night_job(job.id, status)
    runtime.enqueue_night_job("dissertation_review_prepare", "review", "job-2")
    runtime.record_notification_event("draft_ready", "attention_required", "job-1 draft ready for review")
    local = datetime(2026, 8, 7, 7, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    first = runtime.generate_morning_brief(local)
    second = runtime.generate_morning_brief(local)
    assert first.id == second.id
    assert "job-1" in first.completed_overnight
    assert "job-2" in first.awaiting_approval
    assert "Traceback" not in first.runtime_health
    # Regression: attention_required must reflect recorded notification
    # events, not silently duplicate the awaiting_approval job section —
    # otherwise attention_required-severity notifications never surface.
    assert "draft_ready" in first.attention_required
    assert first.attention_required != first.awaiting_approval
