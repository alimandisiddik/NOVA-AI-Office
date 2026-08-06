from __future__ import annotations

from pathlib import Path

from app.memory import MemoryDatabase
from app.nightshift.service import NightShiftService


EXPECTED_TABLES = {
    "runtime_mode_state",
    "night_shift_configuration",
    "night_queue_jobs",
    "night_notification_events",
    "morning_briefs",
    "night_shift_audit_log",
}


def test_fresh_schema_is_additive_and_repeatable(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    service = NightShiftService(database)
    service.initialize()
    service.initialize()
    with database.connection() as connection:
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        mode = connection.execute("SELECT mode FROM runtime_mode_state WHERE id = 1").fetchone()[0]
    assert EXPECTED_TABLES.issubset(tables)
    assert mode == "active"


def test_pre_nightshift_database_data_survives_migration(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    with database.connection() as connection:
        connection.execute("INSERT INTO projects (name, created_at, updated_at) VALUES (?, ?, ?)", ("Legacy", "t", "t"))
    NightShiftService(database).initialize()
    with database.connection() as connection:
        legacy = connection.execute("SELECT name FROM projects").fetchone()[0]
    assert legacy == "Legacy"
