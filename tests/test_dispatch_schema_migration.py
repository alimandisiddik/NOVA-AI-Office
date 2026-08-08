from __future__ import annotations

import sqlite3

import pytest

import app.dispatch.schema as schema_module
from app.dispatch.schema import apply_schema


def _legacy_dispatches(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE dispatches (
            dispatch_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL CHECK (source_type IN ('control_tower_work_item','night_shift_job','telegram_direct')),
            source_id TEXT NOT NULL, agent_id TEXT NOT NULL, capability TEXT NOT NULL,
            payload_ref TEXT NOT NULL DEFAULT '', idempotency_key TEXT NOT NULL, correlation_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','awaiting_approval','approved','dispatching','running','succeeded','failed','cancelled','rejected','timed_out')),
            attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
            requested_by TEXT NOT NULL, result_summary TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(source_type, source_id, idempotency_key)
        )
    """)


def _insert(connection: sqlite3.Connection, source_type: str, dispatch_id: str) -> None:
    connection.execute(
        "INSERT INTO dispatches VALUES (?, ?, 'source', 'workspace_agent', 'draft_only', 'ref', ?, NULL, 'pending', 0, 3, 'user:7', '', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (dispatch_id, source_type, f"key-{dispatch_id}"),
    )


def test_legacy_dispatches_check_constraint_migrates_without_data_loss() -> None:
    connection = sqlite3.connect(":memory:")
    _legacy_dispatches(connection)
    connection.execute("""
        CREATE TABLE approvals (
            approval_id TEXT PRIMARY KEY, dispatch_id TEXT NOT NULL REFERENCES dispatches(dispatch_id) ON DELETE CASCADE,
            requested_action TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'requested', requested_by TEXT NOT NULL,
            resolved_by TEXT, requested_at TEXT NOT NULL, resolved_at TEXT, expires_at TEXT
        )
    """)
    _insert(connection, "telegram_direct", "legacy-1")
    connection.execute("INSERT INTO approvals VALUES ('approval-1', 'legacy-1', 'Approve', 'requested', 'user:7', NULL, '2026-01-01T00:00:00+00:00', NULL, NULL)")
    before = connection.execute("SELECT * FROM dispatches WHERE dispatch_id = 'legacy-1'").fetchone()

    apply_schema(connection)
    after = connection.execute("SELECT * FROM dispatches WHERE dispatch_id = 'legacy-1'").fetchone()

    assert after == before
    assert connection.execute("SELECT dispatch_id FROM approvals WHERE approval_id = 'approval-1'").fetchone()[0] == "legacy-1"
    for index, source_type in enumerate(("control_tower_work_item", "night_shift_job", "telegram_direct", "workspace_action"), start=2):
        _insert(connection, source_type, f"dispatch-{index}")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(connection, "invalid_source", "invalid")
    apply_schema(connection)
    assert connection.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0] == 5


def test_migration_interrupted_mid_rebuild_rolls_back_without_data_loss(monkeypatch) -> None:
    """A crash/exception between the RENAME and the CREATE/COPY/DROP steps
    must not strand data in the renamed-away legacy table with an empty
    'dispatches' silently taking its place; the whole rebuild must roll back
    to the untouched original table."""
    connection = sqlite3.connect(":memory:")
    _legacy_dispatches(connection)
    _insert(connection, "telegram_direct", "legacy-1")
    connection.commit()

    original_create = schema_module._create_dispatches_table
    call_count = {"n": 0}

    def boom(conn: sqlite3.Connection) -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated crash mid-migration")
        return original_create(conn)

    monkeypatch.setattr(schema_module, "_create_dispatches_table", boom)
    with pytest.raises(RuntimeError):
        schema_module._migrate_dispatch_source_constraint(connection)
    monkeypatch.undo()

    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert tables == {"dispatches"}
    assert connection.execute("SELECT dispatch_id FROM dispatches").fetchall() == [("legacy-1",)]

    # A retry (as would happen on the next app startup) must complete cleanly.
    apply_schema(connection)
    assert connection.execute("SELECT dispatch_id, source_type FROM dispatches").fetchall() == [("legacy-1", "telegram_direct")]
    _insert(connection, "workspace_action", "wa-1")  # proves the migrated CHECK constraint is in effect


def test_orphaned_legacy_table_fails_loud_instead_of_silently_discarding_data() -> None:
    """If 'dispatches' is somehow missing while 'dispatches_legacy_8e' still
    holds rows (e.g. a pre-fix crash left the database in that state),
    apply_schema must refuse to silently create an empty 'dispatches' table
    rather than making the legacy data invisible to the application."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE dispatches_legacy_8e (dispatch_id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO dispatches_legacy_8e VALUES ('orphan-1')")
    connection.commit()

    with pytest.raises(sqlite3.OperationalError):
        apply_schema(connection)
