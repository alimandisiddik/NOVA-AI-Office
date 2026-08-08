"""Schema migration coverage for Knowledge Operations."""

from pathlib import Path

from app.knowledge.schema import apply_schema
from app.knowledge.service import KnowledgeService
from app.memory.database import MemoryDatabase


def test_initialize_is_idempotent_for_fresh_database(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "knowledge.sqlite3")
    database.initialize()
    service = KnowledgeService(database)

    service.initialize()
    service.initialize()

    with database.connection() as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        indexes = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }

    assert {"knowledge_sources", "knowledge_items", "knowledge_audit_log"}.issubset(tables)
    assert {"idx_knowledge_items_source", "idx_knowledge_items_tags"}.issubset(indexes)


def test_apply_schema_is_idempotent_when_knowledge_tables_already_exist(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "knowledge.sqlite3")
    database.initialize()
    with database.connection() as connection:
        apply_schema(connection)
        connection.execute(
            """INSERT INTO knowledge_sources
            (title, source_type, origin_system, citation_text, status, created_at, updated_at)
            VALUES ('Legacy source', 'manual', 'manual', 'Legacy citation', 'active', 't', 't')"""
        )

    service = KnowledgeService(database)
    service.initialize()
    service.initialize()

    with database.connection() as connection:
        rows = connection.execute("SELECT title, citation_text FROM knowledge_sources").fetchall()

    assert [(row["title"], row["citation_text"]) for row in rows] == [("Legacy source", "Legacy citation")]
