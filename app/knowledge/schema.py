"""Additive SQLite schema for Knowledge Operations."""

from __future__ import annotations

import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    source_type   TEXT NOT NULL
                  CHECK (source_type IN
                      ('document','note','drive_file','calendar_event','manual','conversation')),
    origin_system TEXT NOT NULL DEFAULT 'manual'
                  CHECK (origin_system IN
                      ('manual','google_drive','google_calendar','dissertation','telegram')),
    origin_ref    TEXT,
    citation_text TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_items (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    project_id   INTEGER,
    work_item_id TEXT,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    tags         TEXT NOT NULL DEFAULT '',
    confidence   TEXT NOT NULL DEFAULT 'MEDIUM' CHECK (confidence IN ('LOW','MEDIUM','HIGH')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_source ON knowledge_items(source_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_tags ON knowledge_items(tags);
CREATE TABLE IF NOT EXISTS knowledge_audit_log (
    id         INTEGER PRIMARY KEY,
    operation  TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    actor      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def apply_schema(connection: sqlite3.Connection) -> None:
    """Create Knowledge Operations tables without changing existing domains."""
    connection.executescript(SCHEMA)
