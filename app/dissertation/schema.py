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

WORKSPACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS dissertation_workspace (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    title TEXT NOT NULL,
    program TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('planning','active','writing','revision','defense_ready','completed')),
    current_focus TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

SOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS dissertation_sources (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('book','journal_article','thesis','conference_paper','report','website','dataset','other')),
    citation_text TEXT NOT NULL,
    locator TEXT,
    status TEXT NOT NULL DEFAULT 'unread' CHECK (status IN ('unread','reviewed','cited','rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dissertation_sources_status ON dissertation_sources(status);

CREATE TABLE IF NOT EXISTS dissertation_source_chapter_links (
    source_id INTEGER NOT NULL REFERENCES dissertation_sources(id) ON DELETE CASCADE,
    chapter_id INTEGER NOT NULL REFERENCES dissertation_chapters(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_id, chapter_id)
);
"""

EVIDENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS dissertation_evidence (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES dissertation_sources(id) ON DELETE RESTRICT,
    chapter_id INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL,
    gap_id INTEGER REFERENCES dissertation_gaps(id) ON DELETE SET NULL,
    summary TEXT NOT NULL,
    locator_detail TEXT,
    confidence TEXT NOT NULL DEFAULT 'MEDIUM'
        CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dissertation_evidence_source_id ON dissertation_evidence(source_id);
CREATE INDEX IF NOT EXISTS idx_dissertation_evidence_chapter_id ON dissertation_evidence(chapter_id);
"""

GAPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS dissertation_gaps (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL,
    description TEXT NOT NULL,
    gap_type TEXT NOT NULL CHECK (gap_type IN ('missing_evidence','conceptual_weakness','literature_gap','methodological_question','validation_needed','supervisor_feedback','other')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','in_progress','resolved','deferred')),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','critical')),
    next_action TEXT NOT NULL DEFAULT '',
    resolution_note TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dissertation_gaps_status ON dissertation_gaps(status);
CREATE INDEX IF NOT EXISTS idx_dissertation_gaps_chapter_id ON dissertation_gaps(chapter_id);
"""

NOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS dissertation_notes (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL,
    source_id INTEGER REFERENCES dissertation_sources(id) ON DELETE SET NULL,
    evidence_id INTEGER REFERENCES dissertation_evidence(id) ON DELETE SET NULL,
    gap_id INTEGER REFERENCES dissertation_gaps(id) ON DELETE SET NULL,
    note_type TEXT NOT NULL CHECK (note_type IN ('analysis','synthesis','supervisor_feedback','question','other')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (chapter_id IS NOT NULL OR source_id IS NOT NULL OR evidence_id IS NOT NULL OR gap_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_dissertation_notes_chapter_id ON dissertation_notes(chapter_id);
"""

LINKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS dissertation_research_task_links (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL,
    gap_id INTEGER REFERENCES dissertation_gaps(id) ON DELETE SET NULL,
    work_item_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dissertation_task_links_chapter_id ON dissertation_research_task_links(chapter_id);

CREATE TABLE IF NOT EXISTS dissertation_decision_links (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER REFERENCES dissertation_chapters(id) ON DELETE SET NULL,
    decision_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
"""

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS dissertation_research_audit_log (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('workspace','source','evidence','note','gap','research_task_link','decision_link')),
    target_id INTEGER NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dissertation_research_audit_target ON dissertation_research_audit_log(target_type, target_id);
"""

CHAPTER_FOCUS_MIGRATION = """
ALTER TABLE dissertation_chapters
ADD COLUMN current_focus TEXT NOT NULL DEFAULT '';
"""

VERSION_STATE_MIGRATION = """
ALTER TABLE dissertation_document_versions
ADD COLUMN version_state TEXT NOT NULL DEFAULT 'original'
CHECK (version_state IN ('original', 'working', 'reviewed', 'approved'));
"""

EVIDENCE_CONFIDENCE_MIGRATION = """
ALTER TABLE dissertation_evidence
ADD COLUMN confidence TEXT NOT NULL DEFAULT 'MEDIUM'
CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH'));
"""



def apply_schema(connection: sqlite3.Connection) -> None:
    """Apply new tables and the version-state column without altering existing data."""
    connection.executescript(SCHEMA)
    connection.executescript(WORKSPACE_SCHEMA)
    connection.executescript(GAPS_SCHEMA)
    connection.executescript(SOURCES_SCHEMA)
    connection.executescript(EVIDENCE_SCHEMA)
    connection.executescript(NOTES_SCHEMA)
    connection.executescript(LINKS_SCHEMA)
    connection.executescript(AUDIT_SCHEMA)

    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(dissertation_document_versions)").fetchall()
    }
    if "version_state" not in columns:
        connection.executescript(VERSION_STATE_MIGRATION)

    chapter_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(dissertation_chapters)").fetchall()
    }
    if "current_focus" not in chapter_columns:
        connection.executescript(CHAPTER_FOCUS_MIGRATION)

    evidence_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(dissertation_evidence)").fetchall()
    }
    if "confidence" not in evidence_columns:
        connection.executescript(EVIDENCE_CONFIDENCE_MIGRATION)
