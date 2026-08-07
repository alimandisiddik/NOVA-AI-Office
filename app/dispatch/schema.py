import sqlite3

def apply_schema(connection: sqlite3.Connection) -> None:
    connection.execute("""
    CREATE TABLE IF NOT EXISTS dispatches (
        dispatch_id     TEXT PRIMARY KEY,
        source_type     TEXT NOT NULL
                        CHECK (source_type IN
                            ('control_tower_work_item','night_shift_job','telegram_direct')),
        source_id       TEXT NOT NULL,
        agent_id        TEXT NOT NULL,
        capability      TEXT NOT NULL,
        payload_ref     TEXT NOT NULL DEFAULT '',
        idempotency_key TEXT NOT NULL,
        correlation_id  TEXT,
        status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending','awaiting_approval','approved','dispatching',
                            'running','succeeded','failed','cancelled','rejected','timed_out'
                        )),
        attempt_count   INTEGER NOT NULL DEFAULT 0,
        max_attempts    INTEGER NOT NULL DEFAULT 3,
        requested_by    TEXT NOT NULL,
        result_summary  TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        UNIQUE(source_type, source_id, idempotency_key)
    )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_dispatches_status ON dispatches(status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_dispatches_source ON dispatches(source_type, source_id)")

    connection.execute("""
    CREATE TABLE IF NOT EXISTS dispatch_attempts (
        id              INTEGER PRIMARY KEY,
        dispatch_id     TEXT NOT NULL REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
        attempt_number  INTEGER NOT NULL,
        status          TEXT NOT NULL
                        CHECK (status IN ('running','succeeded','failed','timed_out','cancelled')),
        started_at      TEXT NOT NULL,
        ended_at        TEXT,
        result_summary  TEXT NOT NULL DEFAULT '',
        UNIQUE(dispatch_id, attempt_number)
    )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_attempts_dispatch_id ON dispatch_attempts(dispatch_id)")

    connection.execute("""
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id      TEXT PRIMARY KEY,
        dispatch_id      TEXT NOT NULL REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
        requested_action TEXT NOT NULL,
        status           TEXT NOT NULL DEFAULT 'requested'
                         CHECK (status IN ('requested','approved','rejected','cancelled','expired')),
        requested_by     TEXT NOT NULL,
        resolved_by      TEXT,
        requested_at     TEXT NOT NULL,
        resolved_at      TEXT,
        expires_at       TEXT
    )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_approvals_dispatch_id ON approvals(dispatch_id)")

    connection.execute("""
    CREATE TABLE IF NOT EXISTS approval_audit (
        id          INTEGER PRIMARY KEY,
        approval_id TEXT NOT NULL REFERENCES approvals(approval_id) ON DELETE CASCADE,
        event       TEXT NOT NULL,
        actor       TEXT NOT NULL,
        detail      TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL
    )
    """)

    connection.execute("""
    CREATE TABLE IF NOT EXISTS dispatch_audit_log (
        id             INTEGER PRIMARY KEY,
        dispatch_id    TEXT NOT NULL REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
        event          TEXT NOT NULL,
        actor          TEXT NOT NULL,
        from_status    TEXT,
        to_status      TEXT,
        correlation_id TEXT,
        detail         TEXT NOT NULL DEFAULT '',
        created_at     TEXT NOT NULL
    )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_dispatch_audit_dispatch_id ON dispatch_audit_log(dispatch_id)")

    connection.execute("""
    CREATE TABLE IF NOT EXISTS dispatch_leases (
        dispatch_id       TEXT PRIMARY KEY REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
        worker_id         TEXT NOT NULL,
        leased_at         TEXT NOT NULL,
        lease_expires_at  TEXT NOT NULL
    )
    """)
