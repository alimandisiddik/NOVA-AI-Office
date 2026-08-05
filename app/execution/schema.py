"""Additive SQLite schema for NOVA execution orchestration.

Rules
-----
- CREATE TABLE IF NOT EXISTS on every table — fully idempotent.
- Never alter, drop, rename, or recreate existing tables.
- Foreign keys reference execution_id so the audit log cascades correctly.
- instruction_hash stores SHA-256 hex of the raw instruction; the raw
  instruction is NEVER persisted.
"""

EXECUTION_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS executions (
    id               INTEGER PRIMARY KEY,
    instruction_hash TEXT    NOT NULL,
    risk_level       TEXT    NOT NULL
                     CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH')),
    workflow_id      TEXT    NOT NULL,
    state            TEXT    NOT NULL DEFAULT 'created'
                     CHECK (state IN (
                         'created', 'awaiting_approval', 'queued',
                         'running', 'completed', 'failed'
                     )),
    approved_by      INTEGER,
    approved_at      TEXT,
    result_summary   TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_audit_log (
    id           INTEGER PRIMARY KEY,
    execution_id INTEGER NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    event        TEXT    NOT NULL,
    actor        TEXT    NOT NULL,
    detail       TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_executions_state
    ON executions(state);
CREATE INDEX IF NOT EXISTS idx_execution_audit_execution_id
    ON execution_audit_log(execution_id);
"""
