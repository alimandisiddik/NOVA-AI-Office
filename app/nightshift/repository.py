"""Parameterized persistence layer for Night Shift Runtime metadata."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.memory.database import MemoryDatabase
from app.nightshift.models import MorningBrief, NightQueueJob, NightSchedule, NotificationEvent, RuntimeModeState


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _job(row: sqlite3.Row) -> NightQueueJob:
    values = dict(row)
    values["approval_required"] = bool(values["approval_required"])
    return NightQueueJob(**values)


class NightShiftRepository:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def get_mode(self) -> RuntimeModeState | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT mode, is_manual_override, updated_at FROM runtime_mode_state WHERE id = 1").fetchone()
        return None if row is None else RuntimeModeState(row["mode"], bool(row["is_manual_override"]), row["updated_at"])

    def set_mode(self, mode: str, manual: bool, actor: str, reason: str, now: str, expected_mode: str | None = None) -> RuntimeModeState | None:
        """Set the runtime mode, always audited.

        If ``expected_mode`` is given, the write is a compare-and-swap: it
        only applies if the currently persisted mode still equals
        ``expected_mode``, and returns ``None`` (no write, no audit row) if a
        concurrent/stale caller already changed it. Manual overrides pass no
        ``expected_mode`` — an explicit human action always wins outright.
        """
        with self.database.connection() as connection:
            old = connection.execute("SELECT mode FROM runtime_mode_state WHERE id = 1").fetchone()
            current = old["mode"] if old else None
            if expected_mode is not None and current != expected_mode:
                return None
            connection.execute(
                "INSERT INTO runtime_mode_state (id, mode, is_manual_override, updated_at) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET mode=excluded.mode, is_manual_override=excluded.is_manual_override, updated_at=excluded.updated_at",
                (mode, int(manual), now),
            )
            connection.execute("INSERT INTO night_shift_audit_log (event, actor, detail, created_at) VALUES (?, ?, ?, ?)",
                ("runtime_mode_changed", actor, f"{current or ''}->{mode};{reason}", now))
        return RuntimeModeState(mode, manual, now)

    def get_schedule(self) -> NightSchedule | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT start_time, end_time, morning_brief_time, timezone, updated_at FROM night_shift_configuration WHERE id = 1").fetchone()
        return None if row is None else NightSchedule(**dict(row))

    def set_schedule(self, start: str, end: str, brief: str, timezone: str, actor: str, now: str) -> NightSchedule:
        with self.database.connection() as connection:
            connection.execute("INSERT INTO night_shift_configuration (id,start_time,end_time,morning_brief_time,timezone,updated_at) VALUES (1,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET start_time=excluded.start_time,end_time=excluded.end_time,morning_brief_time=excluded.morning_brief_time,timezone=excluded.timezone,updated_at=excluded.updated_at", (start,end,brief,timezone,now))
            connection.execute("INSERT INTO night_shift_audit_log (event,actor,detail,created_at) VALUES (?,?,?,?)", ("night_schedule_updated", actor, f"{start}-{end};brief={brief};tz={timezone}", now))
        return NightSchedule(start, end, brief, timezone, now)

    def create_job(self, values: tuple[object, ...]) -> NightQueueJob:
        with self.database.connection() as connection:
            cursor = connection.execute("INSERT INTO night_queue_jobs (job_id,job_type,category,status,requested_at,eligible_after,completed_at,failure_category,approval_required,deduplication_key,audit_metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)", values)
            row = connection.execute("SELECT * FROM night_queue_jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _job(row)

    def get_job(self, job_id: int) -> NightQueueJob | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM night_queue_jobs WHERE id = ?", (job_id,)).fetchone()
        return None if row is None else _job(row)

    def list_jobs(self, status: str | None = None) -> list[NightQueueJob]:
        query, params = ("SELECT * FROM night_queue_jobs", ()) if status is None else ("SELECT * FROM night_queue_jobs WHERE status = ?", (status,))
        with self.database.connection() as connection:
            rows = connection.execute(query + " ORDER BY COALESCE(eligible_after, requested_at), requested_at, id", params).fetchall()
        return [_job(row) for row in rows]

    def transition_job(self, job_id: int, expected: str, target: str, failure: str | None, now: str, actor: str) -> NightQueueJob | None:
        with self.database.connection() as connection:
            cursor = connection.execute("UPDATE night_queue_jobs SET status=?, completed_at=?, failure_category=? WHERE id=? AND status=?", (target, now if target in {"completed", "failed_safely", "rejected"} else None, failure, job_id, expected))
            if cursor.rowcount == 0:
                return None
            connection.execute("INSERT INTO night_shift_audit_log (event,actor,detail,related_job_id,created_at) VALUES (?,?,?,?,?)", ("night_job_transition", actor, f"{expected}->{target}", job_id, now))
            row = connection.execute("SELECT * FROM night_queue_jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row)

    def audit(self, event: str, actor: str, detail: str, now: str) -> None:
        with self.database.connection() as connection:
            connection.execute("INSERT INTO night_shift_audit_log (event,actor,detail,created_at) VALUES (?,?,?,?)", (event,actor,detail,now))

    def notification(self, event_type: str, severity: str, routing: str, summary: str, related_job_id: int | None, now: str) -> NotificationEvent:
        with self.database.connection() as connection:
            cursor = connection.execute("INSERT INTO night_notification_events (event_type,severity,routing,summary,related_job_id,created_at) VALUES (?,?,?,?,?,?)", (event_type,severity,routing,summary,related_job_id,now))
            row = connection.execute("SELECT * FROM night_notification_events WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return NotificationEvent(**dict(row))

    def list_notifications(self, severity: str | None = None) -> list[NotificationEvent]:
        query, params = ("SELECT * FROM night_notification_events", ()) if severity is None else ("SELECT * FROM night_notification_events WHERE severity = ?", (severity,))
        with self.database.connection() as connection:
            rows = connection.execute(query + " ORDER BY created_at, id", params).fetchall()
        return [NotificationEvent(**dict(row)) for row in rows]

    def brief(self, window: str, sections: tuple[str, str, str, str, str], now: str) -> MorningBrief:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM morning_briefs WHERE briefing_window=? ORDER BY version DESC LIMIT 1", (window,)).fetchone()
            if row is None:
                cursor = connection.execute("INSERT INTO morning_briefs (briefing_window,completed_overnight,attention_required,awaiting_approval,failed_safely,runtime_health,generated_at) VALUES (?,?,?,?,?,?,?)", (window,*sections,now))
                row = connection.execute("SELECT * FROM morning_briefs WHERE id=?", (cursor.lastrowid,)).fetchone()
        return MorningBrief(**dict(row))

    def latest_brief(self) -> MorningBrief | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM morning_briefs ORDER BY briefing_window DESC, version DESC LIMIT 1").fetchone()
        return None if row is None else MorningBrief(**dict(row))
