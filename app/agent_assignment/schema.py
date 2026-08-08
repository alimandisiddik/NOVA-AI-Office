"""SQLite schema owned exclusively by the AgentAssignment domain."""

from __future__ import annotations

import sqlite3


def apply_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_assignments (
            assignment_id TEXT PRIMARY KEY,
            work_item_id TEXT NOT NULL,
            requested_capability TEXT NOT NULL CHECK (requested_capability IN
                ('read_only', 'draft_only', 'external_communication', 'publication')),
            assigned_agent_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN
                ('proposed', 'accepted', 'in_progress', 'completed', 'cancelled', 'reassigned')),
            dispatch_id TEXT,
            requested_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_assignments_work_item "
        "ON agent_assignments(work_item_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_assignments_status "
        "ON agent_assignments(status)"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_assignment_audit_log (
            id INTEGER PRIMARY KEY,
            assignment_id TEXT NOT NULL REFERENCES agent_assignments(assignment_id) ON DELETE CASCADE,
            event TEXT NOT NULL,
            actor TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_assignment_audit_assignment "
        "ON agent_assignment_audit_log(assignment_id)"
    )
