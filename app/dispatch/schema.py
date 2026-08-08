import sqlite3


_SOURCE_TYPES = "'control_tower_work_item','night_shift_job','telegram_direct','workspace_action'"


def _dispatch_schema_includes_workspace_action(connection: sqlite3.Connection) -> bool:
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'dispatches'").fetchone()
    if row is None:
        orphaned_legacy = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dispatches_legacy_8e'"
        ).fetchone()
        if orphaned_legacy is not None:
            raise sqlite3.OperationalError(
                "Dispatch schema migration was interrupted: 'dispatches' is missing but "
                "'dispatches_legacy_8e' still holds data. Manual recovery required; refusing "
                "to silently create an empty 'dispatches' table."
            )
        return True
    return "workspace_action" in (row[0] or "")


def _create_dispatches_table(connection: sqlite3.Connection) -> None:
    connection.execute(f"""
    CREATE TABLE IF NOT EXISTS dispatches (
        dispatch_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL CHECK (source_type IN ({_SOURCE_TYPES})),
        source_id TEXT NOT NULL, agent_id TEXT NOT NULL, capability TEXT NOT NULL,
        payload_ref TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL,
        correlation_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','awaiting_approval','approved','dispatching','running','succeeded','failed','cancelled','rejected','timed_out')),
        attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
        requested_by TEXT NOT NULL, result_summary TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(source_type, source_id, idempotency_key)
    )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_dispatches_status ON dispatches(status)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_dispatches_source ON dispatches(source_type, source_id)")


def _migrate_dispatch_source_constraint(connection: sqlite3.Connection) -> None:
    """Rebuild only the legacy CHECK constraint, preserving every dispatch row."""
    if _dispatch_schema_includes_workspace_action(connection):
        return
    # Python's sqlite3 driver does not auto-open a transaction around bare DDL
    # statements, so each ALTER/CREATE/DROP below would otherwise commit
    # individually. An explicit BEGIN/COMMIT makes the whole rebuild atomic:
    # a crash or exception between steps rolls back to the untouched legacy
    # table instead of leaving 'dispatches' renamed away or half-copied.
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("PRAGMA legacy_alter_table = ON")
    # Participate in an already-open caller transaction rather than starting a
    # nested one (sqlite3 rejects BEGIN while a transaction is active); only
    # commit/rollback the transaction this call itself opened.
    owns_transaction = not connection.in_transaction
    try:
        if owns_transaction:
            connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("ALTER TABLE dispatches RENAME TO dispatches_legacy_8e")
            _create_dispatches_table(connection)
            connection.execute("""
                INSERT INTO dispatches (dispatch_id, source_type, source_id, agent_id, capability, payload_ref,
                    idempotency_key, correlation_id, status, attempt_count, max_attempts, requested_by,
                    result_summary, created_at, updated_at)
                SELECT dispatch_id, source_type, source_id, agent_id, capability, payload_ref,
                    idempotency_key, correlation_id, status, attempt_count, max_attempts, requested_by,
                    result_summary, created_at, updated_at FROM dispatches_legacy_8e
            """)
            connection.execute("DROP TABLE dispatches_legacy_8e")
            if owns_transaction:
                connection.execute("COMMIT")
        except BaseException:
            if owns_transaction:
                connection.execute("ROLLBACK")
            raise
    finally:
        connection.execute("PRAGMA legacy_alter_table = OFF")
        connection.execute("PRAGMA foreign_keys = ON")


def apply_schema(connection: sqlite3.Connection) -> None:
    _migrate_dispatch_source_constraint(connection)
    _create_dispatches_table(connection)

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
