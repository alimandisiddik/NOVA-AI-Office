"""SQLite schema owned exclusively by the drafting domain."""

from __future__ import annotations

import sqlite3


def apply_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS prepared_workspace_actions (
            id INTEGER PRIMARY KEY,
            content_type TEXT NOT NULL CHECK (content_type IN
                ('gmail_reply', 'gmail_new', 'docs_memo', 'sheets_change', 'slides_outline')),
            source_ref TEXT,
            title TEXT,
            body_text TEXT,
            body_payload TEXT,
            status TEXT NOT NULL DEFAULT 'prepared'
                CHECK (status IN ('prepared', 'ready_for_action', 'superseded')),
            supersedes_id INTEGER REFERENCES prepared_workspace_actions(id),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_prepared_action_status
            ON prepared_workspace_actions(status);
        CREATE TABLE IF NOT EXISTS drafting_audit_log (
            id INTEGER PRIMARY KEY,
            action_id INTEGER NOT NULL REFERENCES prepared_workspace_actions(id) ON DELETE CASCADE,
            event TEXT NOT NULL,
            actor TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        """
    )
