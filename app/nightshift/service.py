"""Safe, non-executing service interfaces for the Night Shift Runtime."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.nightshift.classifier import APPROVED_OVERNIGHT, CRITICAL_NOTIFY_ONLY, DEFERRED_UNTIL_MORNING, PROHIBITED, classify_job_type
from app.nightshift.models import MorningBrief, NightJobClassification, NightQueueJob, NightSchedule, NotificationEvent, RuntimeModeState
from app.nightshift.repository import NightShiftRepository, utc_now
from app.nightshift.schema import apply_schema
from app.security import SENSITIVE_CONTENT_PATTERN

MODES = frozenset({"active", "night_shift", "quiet", "maintenance"})
JOB_TRANSITIONS = {"queued": frozenset({"preparing", "awaiting_approval", "rejected", "failed_safely"}), "preparing": frozenset({"validating", "failed_safely"}), "validating": frozenset({"draft_saved", "failed_safely"}), "draft_saved": frozenset({"awaiting_approval", "failed_safely"}), "awaiting_approval": frozenset({"completed", "rejected", "failed_safely"}), "completed": frozenset(), "rejected": frozenset(), "failed_safely": frozenset()}

class NightShiftError(RuntimeError): pass
class InvalidRuntimeModeError(NightShiftError): pass
class InvalidScheduleError(NightShiftError): pass
class UnregisteredJobTypeError(NightShiftError): pass
class ProhibitedNightJobError(UnregisteredJobTypeError): pass
class DuplicateNightJobError(NightShiftError): pass
class InvalidNightJobTransitionError(NightShiftError): pass
class NightJobNotFoundError(NightShiftError): pass

class NightShiftService:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database, self.repository = database, NightShiftRepository(database)

    def initialize(self) -> None:
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
            if self.repository.get_mode() is None:
                self.repository.set_mode("active", False, "system:initialize", "initial state", utc_now())
            if self.repository.get_schedule() is None:
                self.repository.set_schedule("22:00", "06:00", "07:00", "Asia/Jakarta", "system:initialize", utc_now())
        except sqlite3.Error as error:
            raise MemoryDatabaseError("Night Shift Runtime schema initialization failed") from error

    def get_runtime_mode(self) -> RuntimeModeState:
        state = self.repository.get_mode()
        if state is None or state.mode not in MODES:
            raise InvalidRuntimeModeError("Runtime mode is unavailable or unsafe")
        return state

    def set_runtime_mode(self, mode: str, actor: str, reason: str) -> RuntimeModeState:
        self._mode(mode); self._text(actor, "actor"); self._text(reason, "reason"); self._safe(actor); self._safe(reason)
        # Any call through this manual entry point is, by definition, a manual
        # override — including a manual return to "active" — so it must stay
        # sticky against the next automatic tick() the same way quiet/maintenance do.
        return self.repository.set_mode(mode, True, actor, reason, utc_now())

    set_mode = set_runtime_mode

    def get_status(self) -> RuntimeModeState:
        """Compatibility status interface reserved for Sprint 5B."""
        return self.get_runtime_mode()

    def wake_now(self, actor: str) -> RuntimeModeState:
        """Leave night shift without overriding quiet or maintenance."""
        self._text(actor, "actor")
        self._safe(actor)
        if self.get_runtime_mode().mode != "night_shift":
            raise InvalidRuntimeModeError("Wake is only valid from night_shift")
        # Manual wake must stay sticky even if still inside the night-shift
        # window, otherwise the next tick() immediately reverts it.
        return self.repository.set_mode("active", True, actor, "manual wake", utc_now())

    def get_night_schedule(self) -> NightSchedule:
        schedule = self.repository.get_schedule()
        if schedule is None: raise InvalidScheduleError("Night-shift schedule is unavailable")
        return schedule

    def update_night_schedule(self, start_time: str, end_time: str, morning_brief_time: str, timezone: str = "Asia/Jakarta", actor: str = "system") -> NightSchedule:
        for value in (start_time, end_time, morning_brief_time): self._time(value)
        if start_time == end_time: raise InvalidScheduleError("Start and end time must differ")
        self._timezone(timezone); self._text(actor, "actor"); self._safe(actor)
        return self.repository.set_schedule(start_time, end_time, morning_brief_time, timezone, actor, utc_now())

    def classify_night_job(self, job_type: str) -> NightJobClassification:
        classification = classify_job_type(job_type)
        if classification is None: raise UnregisteredJobTypeError("Night job type is not registered")
        return classification

    def enqueue_night_job(self, job_type: str, deduplication_key: str, job_id: str, audit_metadata: str = "{}", eligible_after: str | None = None) -> NightQueueJob:
        classification = self.classify_night_job(job_type)
        if classification.disposition == PROHIBITED:
            self.repository.audit("night_job_rejected", "system:policy", "prohibited job type", utc_now())
            raise ProhibitedNightJobError("Night job type is prohibited")
        if self.get_runtime_mode().mode == "maintenance": raise NightShiftError("Maintenance mode rejects new overnight work")
        self._text(deduplication_key, "deduplication key"); self._text(job_id, "job id"); self._safe(deduplication_key); self._safe(job_id); self._metadata(audit_metadata)
        status = "awaiting_approval" if classification.disposition == DEFERRED_UNTIL_MORNING else "queued"
        try:
            return self.repository.create_job((job_id, job_type, classification.category, status, utc_now(), eligible_after, None, None, int(status == "awaiting_approval"), deduplication_key, audit_metadata))
        except sqlite3.IntegrityError as error:
            raise DuplicateNightJobError("Duplicate night job rejected") from error

    enqueue = enqueue_night_job

    def list_night_jobs(self, status: str | None = None) -> list[NightQueueJob]: return self.repository.list_jobs(status)
    list_queue = list_night_jobs

    def transition_night_job(self, job_id: int, target_status: str, actor: str = "system") -> NightQueueJob:
        job = self.repository.get_job(job_id)
        if job is None: raise NightJobNotFoundError("Night job not found")
        if target_status not in JOB_TRANSITIONS.get(job.status, frozenset()): raise InvalidNightJobTransitionError("Night job transition is not permitted")
        changed = self.repository.transition_job(job_id, job.status, target_status, "safe_failure" if target_status == "failed_safely" else None, utc_now(), actor)
        if changed is None: raise InvalidNightJobTransitionError("Night job changed concurrently")
        return changed

    def record_notification_event(self, event_type: str, severity: str, summary: str = "", related_job_id: int | None = None) -> NotificationEvent:
        if severity not in {"informational", "attention_required", "critical"}: raise NightShiftError("Invalid notification severity")
        # Membership in the explicit approved-critical set, not agreement with
        # classify_notification()'s fail-closed default — an unregistered
        # event_type also defaults to "critical" there, which would otherwise
        # let a caller self-assign critical severity for any made-up event_type.
        if severity == "critical" and event_type not in self._CRITICAL_EVENT_TYPES:
            raise NightShiftError("Routine event cannot be classified as critical")
        self._safe(summary)
        routing = {"informational": "morning_brief", "attention_required": "prioritized_morning_brief", "critical": "immediate_eligible"}[severity]
        return self.repository.notification(event_type, severity, routing, summary, related_job_id, utc_now())

    notify = record_notification_event

    _CRITICAL_EVENT_TYPES = frozenset({
        "probable_data_loss", "security_incident", "database_corruption",
        "essential_service_repeated_failure", "classification_failure",
    })
    _ATTENTION_EVENT_TYPES = frozenset({
        "deferred_job", "draft_ready", "test_failure", "provider_rate_limit",
        "conversion_error", "nonessential_service_failure",
    })

    @classmethod
    def classify_notification(cls, event_type: str) -> str:
        """Classify incident signals without treating routine failures as urgent.

        Unknown event types fail closed to "critical" for this classifier's
        own advisory purpose, but record_notification_event() never trusts
        this alone for severity="critical" — see its own approved-set check.
        """
        if event_type in cls._CRITICAL_EVENT_TYPES:
            return "critical"
        if event_type in cls._ATTENTION_EVENT_TYPES:
            return "attention_required"
        return "critical"

    def generate_morning_brief(self, now: datetime | None = None) -> MorningBrief:
        schedule = self.get_night_schedule(); local = (now or datetime.now(ZoneInfo(schedule.timezone))).astimezone(ZoneInfo(schedule.timezone))
        window = local.date().isoformat()
        jobs = self.repository.list_jobs()
        attention_events = self.repository.list_notifications("attention_required")
        sections = (
            self._summary_jobs([job for job in jobs if job.status == "completed"]),
            self._summary_notifications(attention_events),
            self._summary_jobs([job for job in jobs if job.status == "awaiting_approval"]),
            self._summary_jobs([job for job in jobs if job.status == "failed_safely"]),
            json.dumps({"mode": self.get_runtime_mode().mode, "executor": "not_implemented"}, sort_keys=True),
        )
        return self.repository.brief(window, sections, utc_now())

    def get_latest_morning_brief(self) -> MorningBrief | None: return self.repository.latest_brief()

    def register_job_executor(self, job_type: str, executor: object) -> None:
        """Validate a future executor registration without retaining or running it."""
        if executor is None:
            raise NightShiftError("Executor is required")
        classification = self.classify_night_job(job_type)
        if classification.disposition == PROHIBITED:
            raise ProhibitedNightJobError("Prohibited job type cannot have an executor")

    def tick(self, now: datetime) -> None:
        schedule = self.get_night_schedule(); local = now.astimezone(ZoneInfo(schedule.timezone)); state = self.get_runtime_mode()
        if not state.is_manual_override:
            inside = self.is_night_shift_window(local, schedule)
            target = "night_shift" if inside else "active"
            if state.mode != target:
                # Compare-and-swap against the mode just read: if a manual
                # override raced in between, silently skip rather than
                # clobber the fresher state — the next tick re-evaluates.
                self.repository.set_mode(target, False, "system:scheduler", "scheduled boundary", utc_now(), expected_mode=state.mode)
        if local.strftime("%H:%M") == schedule.morning_brief_time: self.generate_morning_brief(local)

    @staticmethod
    def is_night_shift_window(now: datetime, schedule: NightSchedule) -> bool:
        current = now.astimezone(ZoneInfo(schedule.timezone)).strftime("%H:%M")
        return schedule.start_time <= current < schedule.end_time if schedule.start_time < schedule.end_time else current >= schedule.start_time or current < schedule.end_time

    @staticmethod
    def _time(value: str) -> None:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value): raise InvalidScheduleError("Time must use HH:MM (24-hour) format")
    @staticmethod
    def _timezone(value: str) -> None:
        try: ZoneInfo(value)
        except ZoneInfoNotFoundError as error: raise InvalidScheduleError("Timezone is unavailable") from error
    @staticmethod
    def _mode(value: str) -> None:
        if value not in MODES: raise InvalidRuntimeModeError("Unknown runtime mode rejected")
    @staticmethod
    def _text(value: str, label: str) -> None:
        if not isinstance(value, str) or not value.strip(): raise NightShiftError(f"{label} is required")
    @staticmethod
    def _safe(value: str) -> None:
        if value and (SENSITIVE_CONTENT_PATTERN.search(value) or "traceback" in value.lower()): raise NightShiftError("Sensitive or raw error content is not permitted")
    @classmethod
    def _metadata(cls, value: str) -> None:
        cls._safe(value)
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise NightShiftError("Audit metadata must be a JSON object") from error
        if not isinstance(parsed, dict) or {"prompt", "content", "document", "response", "exception"} & set(parsed):
            raise NightShiftError("Audit metadata may contain identifiers only")
    @staticmethod
    def _summary_jobs(jobs: list[NightQueueJob]) -> str:
        return json.dumps([{"job_id": job.job_id, "job_type": job.job_type, "status": job.status} for job in jobs], sort_keys=True)

    @staticmethod
    def _summary_notifications(events: list[NotificationEvent]) -> str:
        return json.dumps([{"event_type": event.event_type, "summary": event.summary} for event in events], sort_keys=True)
