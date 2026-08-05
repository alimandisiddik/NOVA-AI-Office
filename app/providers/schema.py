"""Database schema for provider request auditing."""

PROVIDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_request_audit (
    request_id      TEXT PRIMARY KEY,
    execution_id    INTEGER, -- NULLABLE for direct /ask
    user_id         INTEGER NOT NULL,
    provider_id     TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    workflow_id     TEXT NOT NULL,
    role_id         TEXT NOT NULL,
    status          TEXT NOT NULL,
    prompt_hash     TEXT NOT NULL,
    response_size   INTEGER NOT NULL,
    latency_ms      INTEGER NOT NULL,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    error_category  TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT NOT NULL
);
"""
