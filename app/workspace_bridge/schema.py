"""Additive SQLite schema owned by the Workspace bridge."""

from __future__ import annotations

import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_source_refs (
    id                    INTEGER PRIMARY KEY,
    source_system         TEXT NOT NULL CHECK (source_system IN ('gmail','calendar','drive')),
    account_namespace     TEXT NOT NULL,
    external_source_type  TEXT NOT NULL CHECK (external_source_type IN ('message','thread','event','file')),
    external_source_id    TEXT NOT NULL,
    content_fingerprint   TEXT,
    candidate_summary     TEXT NOT NULL,
    target_type           TEXT CHECK (target_type IN ('work_item','decision','knowledge_item',NULL)),
    target_id             TEXT,
    status                TEXT NOT NULL DEFAULT 'candidate'
                          CHECK (status IN ('candidate','committed','dismissed')),
    created_by            TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_source_identity
    ON workspace_source_refs(source_system, account_namespace, external_source_type, external_source_id);
CREATE INDEX IF NOT EXISTS idx_workspace_source_status ON workspace_source_refs(status);
CREATE TABLE IF NOT EXISTS workspace_bridge_audit_log (
    id         INTEGER PRIMARY KEY,
    ref_id     INTEGER NOT NULL REFERENCES workspace_source_refs(id) ON DELETE CASCADE,
    event      TEXT NOT NULL,
    actor      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def apply_schema(connection: sqlite3.Connection) -> None:
    """Install the bridge's additive schema."""
    connection.executescript(SCHEMA)
