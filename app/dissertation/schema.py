"""Additive, idempotent SQLite schema and migrations for Dissertation Workspace."""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS dissertation_chapters (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'in_review', 'revised', 'final')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dissertation_subchapters (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER NOT NULL REFERENCES dissertation_chapters(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'in_review', 'revised', 'final')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dissertation_document_versions (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('chapter', 'subchapter')),
    target_id INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    version_state TEXT NOT NULL DEFAULT 'original'
        CHECK (version_state IN ('original', 'working', 'reviewed', 'approved')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dissertation_paragraph_maps (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES dissertation_document_versions(id) ON DELETE CASCADE,
    paragraph_ordinal INTEGER NOT NULL,
    stable_paragraph_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(version_id, paragraph_ordinal)
);

CREATE TABLE IF NOT EXISTS dissertation_review_jobs (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('chapter', 'subchapter')),
    target_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'in_progress', 'completed', 'failed')),
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dissertation_revision_log (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('chapter', 'subchapter')),
    target_id INTEGER NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS delete_chapter_versions_before_chapter
BEFORE DELETE ON dissertation_chapters
BEGIN
    DELETE FROM dissertation_document_versions
    WHERE target_type = 'chapter' AND target_id = OLD.id;
    DELETE FROM dissertation_document_versions
    WHERE target_type = 'subchapter'
      AND target_id IN (
          SELECT id FROM dissertation_subchapters WHERE chapter_id = OLD.id
      );
END;

CREATE TRIGGER IF NOT EXISTS delete_subchapter_versions_before_subchapter
BEFORE DELETE ON dissertation_subchapters
BEGIN
    DELETE FROM dissertation_document_versions
    WHERE target_type = 'subchapter' AND target_id = OLD.id;
END;
"""

VERSION_STATE_MIGRATION = """
ALTER TABLE dissertation_document_versions
ADD COLUMN version_state TEXT NOT NULL DEFAULT 'original'
CHECK (version_state IN ('original', 'working', 'reviewed', 'approved'));
"""


def apply_schema(connection: sqlite3.Connection) -> None:
    """Apply new tables and the version-state column without altering existing data."""
    connection.executescript(SCHEMA)
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(dissertation_document_versions)").fetchall()
    }
    if "version_state" not in columns:
        connection.executescript(VERSION_STATE_MIGRATION)
