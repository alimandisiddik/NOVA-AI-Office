"""Migration and schema tests for Sprint 3 execution tables."""

from __future__ import annotations

import pytest

from app.memory.database import MemoryDatabase
from app.execution.repository import ExecutionRepository


@pytest.fixture
def repo(tmp_path) -> ExecutionRepository:
    db = MemoryDatabase(tmp_path / "test.db")
    db.initialize()           # existing memory schema
    repo = ExecutionRepository(db)
    repo.initialize()         # additive execution schema
    return repo


@pytest.fixture
def mem_db(tmp_path) -> MemoryDatabase:
    db = MemoryDatabase(tmp_path / "test.db")
    db.initialize()
    return db


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_execution_schema_creates_executions_table(repo) -> None:
    with repo._db.connection() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "executions" in tables


def test_execution_schema_creates_audit_log_table(repo) -> None:
    with repo._db.connection() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "execution_audit_log" in tables


def test_existing_tables_remain_intact(repo) -> None:
    with repo._db.connection() as conn:
        tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    for existing in ("projects", "tasks", "notes", "decisions", "sessions"):
        assert existing in tables, f"Existing table '{existing}' was removed"


def test_schema_initialization_is_idempotent(tmp_path) -> None:
    db = MemoryDatabase(tmp_path / "idem.db")
    db.initialize()
    repo = ExecutionRepository(db)
    repo.initialize()
    repo.initialize()   # second call must not raise
    repo.initialize()   # third call must not raise


def test_existing_data_survives_schema_migration(tmp_path) -> None:
    from app.memory.services import WorkspaceMemoryService
    db = MemoryDatabase(tmp_path / "data.db")
    svc = WorkspaceMemoryService(db)
    svc.initialize()
    project = svc.create_project("Migration Test Project")

    # Now apply execution schema
    repo = ExecutionRepository(db)
    repo.initialize()

    # Existing project must still be readable
    projects = svc.list_projects()
    assert any(p.name == "Migration Test Project" for p in projects)
