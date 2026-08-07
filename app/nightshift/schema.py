"""Additive, idempotent SQLite schema for Night Shift Runtime."""

from __future__ import annotations

import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_mode_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL CHECK (mode IN ('active','night_shift','quiet','maintenance')),
    is_manual_override INTEGER NOT NULL DEFAULT 0 CHECK (is_manual_override IN (0,1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS night_shift_configuration (
    id INTEGER PRIMARY KEY CHECK (id = 1), start_time TEXT NOT NULL,
    end_time TEXT NOT NULL, morning_brief_time TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Jakarta', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS night_queue_jobs (
    id INTEGER PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, job_type TEXT NOT NULL,
    category TEXT NOT NULL, status TEXT NOT NULL,
    requested_at TEXT NOT NULL, eligible_after TEXT, completed_at TEXT,
    failure_category TEXT, approval_required INTEGER NOT NULL DEFAULT 0 CHECK (approval_required IN (0,1)),
    deduplication_key TEXT NOT NULL, audit_metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(job_type, deduplication_key)
);
CREATE TABLE IF NOT EXISTS night_notification_events (
    id INTEGER PRIMARY KEY, event_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('informational','attention_required','critical')),
    routing TEXT NOT NULL CHECK (routing IN ('morning_brief','prioritized_morning_brief','immediate_eligible')),
    summary TEXT NOT NULL DEFAULT '', related_job_id INTEGER REFERENCES night_queue_jobs(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS morning_briefs (
    id INTEGER PRIMARY KEY, briefing_window TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    completed_overnight TEXT NOT NULL DEFAULT '[]', attention_required TEXT NOT NULL DEFAULT '[]',
    awaiting_approval TEXT NOT NULL DEFAULT '[]', failed_safely TEXT NOT NULL DEFAULT '[]',
    runtime_health TEXT NOT NULL DEFAULT '{}', generated_at TEXT NOT NULL,
    UNIQUE(briefing_window, version)
);
CREATE TABLE IF NOT EXISTS night_shift_audit_log (
    id INTEGER PRIMARY KEY, event TEXT NOT NULL, actor TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '', related_job_id INTEGER REFERENCES night_queue_jobs(id), created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_night_queue_status_requested ON night_queue_jobs(status, eligible_after, requested_at, id);
CREATE INDEX IF NOT EXISTS idx_night_notifications_created ON night_notification_events(created_at);
CREATE INDEX IF NOT EXISTS idx_night_audit_created ON night_shift_audit_log(created_at);
"""


# Sprint 5F additive columns on the pre-existing 5A.1 `night_queue_jobs` table.
# Guarded by PRAGMA table_info(...) so a repeated call never re-runs ALTER TABLE
# for a column that already exists, and any genuinely unexpected
# sqlite3.OperationalError (a real schema problem, not "column already exists")
# propagates instead of being swallowed.
_ADDITIVE_COLUMNS: dict[str, str] = {
    "dispatch_id": "TEXT",
    "lease_worker_id": "TEXT",
    "lease_expires_at": "TEXT",
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
    "approval_id": "TEXT",
}


def apply_schema(connection: sqlite3.Connection) -> None:
    """Safely apply only additive DDL to a pre-existing NOVA database."""
    connection.executescript(SCHEMA)

    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(night_queue_jobs)").fetchall()}
    for column, definition in _ADDITIVE_COLUMNS.items():
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE night_queue_jobs ADD COLUMN {column} {definition}")

    connection.execute("CREATE INDEX IF NOT EXISTS idx_night_queue_lease ON night_queue_jobs(status, lease_expires_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_night_queue_dispatch ON night_queue_jobs(dispatch_id)")
