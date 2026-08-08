"""Additive persistence for the metadata-only WorkspaceAction aggregate."""

from __future__ import annotations

import sqlite3


def apply_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
    CREATE TABLE IF NOT EXISTS workspace_actions (
        id INTEGER PRIMARY KEY,
        action_type TEXT NOT NULL CHECK (action_type = 'create_docs_file'),
        prepared_action_id INTEGER NOT NULL,
        dispatch_id TEXT UNIQUE,
        approval_id TEXT UNIQUE,
        payload_schema_version INTEGER NOT NULL,
        payload_fingerprint TEXT NOT NULL,
        account_namespace TEXT,
        idempotency_key TEXT NOT NULL UNIQUE,
        correlation_id TEXT NOT NULL,
        requested_chat_id TEXT,
        status TEXT NOT NULL CHECK (status IN ('prepared','awaiting_approval','approved','executing','succeeded','failed','rejected','cancelled','outcome_unknown')),
        version INTEGER NOT NULL DEFAULT 1,
        provider_reference TEXT,
        failure_category TEXT,
        requested_at TEXT NOT NULL,
        approved_at TEXT,
        executing_at TEXT,
        completed_at TEXT,
        updated_at TEXT NOT NULL
    )
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(workspace_actions)").fetchall()}
    if "requested_chat_id" not in columns:
        connection.execute("ALTER TABLE workspace_actions ADD COLUMN requested_chat_id TEXT")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_workspace_actions_status ON workspace_actions(status)")
    connection.execute("""
    CREATE TABLE IF NOT EXISTS workspace_action_audit_log (
        id INTEGER PRIMARY KEY,
        action_id INTEGER NOT NULL REFERENCES workspace_actions(id) ON DELETE CASCADE,
        event TEXT NOT NULL,
        actor TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """)
