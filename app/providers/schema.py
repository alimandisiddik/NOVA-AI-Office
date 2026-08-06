"""Additive SQLite schema and migrations for provider auditing."""

PROVIDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_request_audit (
    request_id      TEXT PRIMARY KEY,
    execution_id    INTEGER,
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

CREATE TABLE IF NOT EXISTS provider_request_attempts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL,
    model_id        TEXT NOT NULL,
    latency_ms      INTEGER NOT NULL,
    error_category  TEXT,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(request_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS idx_provider_attempts_request_id
    ON provider_request_attempts(request_id);
"""

MIGRATIONS = (
    "ALTER TABLE provider_request_audit ADD COLUMN initial_model_id TEXT;",
    "ALTER TABLE provider_request_audit ADD COLUMN final_model_id TEXT;",
    "ALTER TABLE provider_request_audit ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 1;",
    "ALTER TABLE provider_request_audit ADD COLUMN fallback_used INTEGER NOT NULL DEFAULT 0;",
    "ALTER TABLE provider_request_audit ADD COLUMN fallback_reason TEXT;",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_attempts_request_number "
    "ON provider_request_attempts(request_id, attempt_number);",
)
