"""Additive SQLite schema owned by manual external-message intake."""

from __future__ import annotations

import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS external_message_intake (
    id                  INTEGER PRIMARY KEY,
    source_channel      TEXT NOT NULL DEFAULT 'whatsapp_manual'
                        CHECK (source_channel IN ('whatsapp_manual')),
    telegram_update_id  TEXT,
    raw_text            TEXT NOT NULL,
    classification      TEXT NOT NULL DEFAULT 'uncertain'
                        CHECK (classification IN ('task','note','knowledge','follow_up','uncertain')),
    status              TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK (status IN ('pending_review','confirmed','dismissed')),
    target_type         TEXT CHECK (target_type IN ('work_item','note','knowledge_item',NULL)),
    target_id           TEXT,
    content_fingerprint TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_intake_telegram_update
    ON external_message_intake(telegram_update_id)
    WHERE telegram_update_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_intake_status ON external_message_intake(status);
CREATE INDEX IF NOT EXISTS idx_intake_content_fingerprint ON external_message_intake(content_fingerprint);
CREATE TABLE IF NOT EXISTS intake_audit_log (
    id         INTEGER PRIMARY KEY,
    intake_id  INTEGER NOT NULL REFERENCES external_message_intake(id) ON DELETE CASCADE,
    event      TEXT NOT NULL,
    actor      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def apply_schema(connection: sqlite3.Connection) -> None:
    """Create intake-owned tables without changing existing domains."""
    connection.executescript(SCHEMA)
