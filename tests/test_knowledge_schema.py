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


def test_apply_schema_widens_legacy_source_type_check_without_losing_rows(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "legacy-knowledge.sqlite3")
    database.initialize()
    with database.connection() as connection:
        connection.executescript(
            """
            CREATE TABLE knowledge_sources (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                source_type TEXT NOT NULL CHECK (source_type IN ('manual')),
                origin_system TEXT NOT NULL DEFAULT 'manual', origin_ref TEXT,
                citation_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE knowledge_items (
                id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
                project_id INTEGER, work_item_id TEXT, title TEXT NOT NULL, summary TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '', confidence TEXT NOT NULL DEFAULT 'MEDIUM',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO knowledge_sources VALUES (1, 'Legacy', 'manual', 'manual', NULL, 'Citation', 'active', 't', 't');
            INSERT INTO knowledge_items VALUES (1, 1, NULL, NULL, 'Item', 'Summary', '', 'MEDIUM', 't', 't');
            """
        )
        apply_schema(connection)
        connection.execute(
            """INSERT INTO knowledge_sources
            (title, source_type, origin_system, citation_text, status, created_at, updated_at)
            VALUES ('Gmail', 'gmail_message', 'manual', 'Citation', 'active', 't', 't')"""
        )
        rows = connection.execute("SELECT title FROM knowledge_sources ORDER BY id").fetchall()
        items = connection.execute("SELECT source_id FROM knowledge_items").fetchall()

    assert [row["title"] for row in rows] == ["Legacy", "Gmail"]
    assert [row["source_id"] for row in items] == [1]
