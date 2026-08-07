from pathlib import Path
import sqlite3
import pytest
from app.memory import MemoryDatabase
from app.nightshift.schema import apply_schema

def test_fresh_schema_migration(tmp_path: Path):
    database = MemoryDatabase(tmp_path / "fresh.db")
    database.initialize()
    with database.connection() as connection:
        apply_schema(connection)
        cols = {row[1] for row in connection.execute("PRAGMA table_info(night_queue_jobs)").fetchall()}
        assert "dispatch_id" in cols
        assert "attempt_count" in cols
        assert "approval_id" in cols

def test_initialize_twice(tmp_path: Path):
    database = MemoryDatabase(tmp_path / "twice.db")
    database.initialize()
    with database.connection() as connection:
        apply_schema(connection)
        apply_schema(connection) # should not fail

def test_migrate_real_pre_5f_schema(tmp_path: Path):
    database = MemoryDatabase(tmp_path / "pre5f.db")
    database.initialize()
    with database.connection() as connection:
        # simulate pre-5f state by creating without the new columns first
        # But SCHEMA constant in schema.py already has the original tables.
        # We can just apply SCHEMA first.
        from app.nightshift.schema import SCHEMA
        connection.executescript(SCHEMA)

        # Insert a job
        connection.execute("INSERT INTO night_queue_jobs (job_id, job_type, category, status, requested_at, approval_required, deduplication_key) VALUES ('j1', 't1', 'c1', 'queued', '2026-01-01', 0, 'd1')")

        # Now apply migration
        apply_schema(connection)

        # existing row preserved
        row = connection.execute("SELECT dispatch_id, attempt_count FROM night_queue_jobs WHERE job_id = 'j1'").fetchone()
        assert row["dispatch_id"] is None
        assert row["attempt_count"] == 0

def test_partially_migrated_schema(tmp_path: Path):
    database = MemoryDatabase(tmp_path / "partial.db")
    database.initialize()
    with database.connection() as connection:
        from app.nightshift.schema import SCHEMA
        connection.executescript(SCHEMA)

        # partially migrate
        connection.execute("ALTER TABLE night_queue_jobs ADD COLUMN dispatch_id TEXT")

        # Now apply migration
        apply_schema(connection)
        cols = {row[1] for row in connection.execute("PRAGMA table_info(night_queue_jobs)").fetchall()}
        assert "attempt_count" in cols
        assert "dispatch_id" in cols

def test_forced_sqlite_failure_not_swallowed(tmp_path: Path):
    database = MemoryDatabase(tmp_path / "fail.db")
    database.initialize()

    class BrokenConnection:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("Forced failure")
        def executescript(self, *args, **kwargs):
            pass

    with pytest.raises(sqlite3.OperationalError, match="Forced failure"):
        apply_schema(BrokenConnection())
