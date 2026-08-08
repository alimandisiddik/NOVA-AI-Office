"""SQLite schema owned exclusively by the conversation domain."""

from __future__ import annotations

import sqlite3


def apply_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversation_pending_interactions (
            interaction_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            source_command TEXT NOT NULL,
            prompt_summary TEXT NOT NULL,
            choices_json TEXT NOT NULL,
            max_risk_level TEXT NOT NULL
                CHECK (max_risk_level IN ('low', 'ambiguous_only', 'high')),
            status TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'resolved', 'expired', 'superseded')),
            resolved_choice_index INTEGER,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_open_per_chat
            ON conversation_pending_interactions(chat_id)
            WHERE status = 'open';
        CREATE INDEX IF NOT EXISTS idx_pending_status
            ON conversation_pending_interactions(status);
        CREATE TABLE IF NOT EXISTS conversation_audit_log (
            id INTEGER PRIMARY KEY,
            interaction_id TEXT NOT NULL REFERENCES conversation_pending_interactions(interaction_id)
                ON DELETE CASCADE,
            event TEXT NOT NULL,
            actor TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        """
    )
