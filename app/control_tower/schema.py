"""Additive, idempotent SQLite schema for the Executive Control Tower."""

from __future__ import annotations
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS control_tower_work_items (
    item_id TEXT PRIMARY KEY, project_id INTEGER, category TEXT NOT NULL, title TEXT NOT NULL,
    summary TEXT, priority_score INTEGER NOT NULL DEFAULT 0, urgency INTEGER NOT NULL DEFAULT 0,
    importance INTEGER NOT NULL DEFAULT 0, deadline TEXT, clarification_needs TEXT,
    recommended_route TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control_tower_work_dependencies (
    item_id TEXT NOT NULL REFERENCES control_tower_work_items(item_id) ON DELETE CASCADE,
    depends_on_item_id TEXT NOT NULL REFERENCES control_tower_work_items(item_id) ON DELETE RESTRICT,
    PRIMARY KEY (item_id, depends_on_item_id), CHECK (item_id <> depends_on_item_id)
);
CREATE TABLE IF NOT EXISTS control_tower_decisions (
    decision_id TEXT PRIMARY KEY, project_id INTEGER, summary TEXT NOT NULL, rationale TEXT NOT NULL,
    impact TEXT NOT NULL, approved_by TEXT NOT NULL, effective_date TEXT NOT NULL, status TEXT NOT NULL,
    supersedes TEXT REFERENCES control_tower_decisions(decision_id),
    superseded_by TEXT REFERENCES control_tower_decisions(decision_id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control_tower_approval_links (
    approval_id TEXT PRIMARY KEY, source_system TEXT NOT NULL, source_item_id TEXT NOT NULL,
    requested_action TEXT NOT NULL, status TEXT NOT NULL, requested_at TEXT NOT NULL,
    resolved_at TEXT, resolved_by TEXT
);
CREATE TABLE IF NOT EXISTS control_tower_audit_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT, operation TEXT NOT NULL, entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL, from_state TEXT, to_state TEXT, actor TEXT NOT NULL,
    correlation_id TEXT, outcome TEXT NOT NULL DEFAULT 'success', metadata TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ct_work_status_deadline ON control_tower_work_items(status, deadline, priority_score);
CREATE INDEX IF NOT EXISTS idx_ct_work_project ON control_tower_work_items(project_id);
CREATE INDEX IF NOT EXISTS idx_ct_dependencies_depends_on ON control_tower_work_dependencies(depends_on_item_id);
CREATE INDEX IF NOT EXISTS idx_ct_approvals_status ON control_tower_approval_links(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_ct_decisions_project ON control_tower_decisions(project_id, created_at);
"""

_AUDIT_ADDITIONS = {
    "operation": "TEXT NOT NULL DEFAULT ''",
    "correlation_id": "TEXT",
    "outcome": "TEXT NOT NULL DEFAULT 'success'",
    "metadata": "TEXT NOT NULL DEFAULT '{}'",
}


def apply_schema(connection: sqlite3.Connection) -> None:
    """Apply only repeat-safe creates and additive audit-column migrations."""
    connection.executescript(SCHEMA)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(control_tower_audit_log)")}
    for column, definition in _AUDIT_ADDITIONS.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE control_tower_audit_log ADD COLUMN {column} {definition}")
