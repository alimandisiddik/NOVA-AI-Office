"""Schema tests for the additive Dissertation Workspace migration."""

from pathlib import Path

from app.dissertation.service import DissertationService
from app.memory.database import MemoryDatabase

SHA256 = "a" * 64


def test_initialize_is_idempotent_and_preserves_existing_memory_schema(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    service = DissertationService(database)

    service.initialize()
    chapter = service.create_chapter("Unit A", 1)
    service.initialize()

    with database.connection() as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        chapter_count = connection.execute("SELECT COUNT(*) FROM dissertation_chapters").fetchone()[0]

    assert "projects" in tables
    assert {
        "dissertation_chapters",
        "dissertation_subchapters",
        "dissertation_document_versions",
        "dissertation_paragraph_maps",
        "dissertation_review_jobs",
        "dissertation_revision_log",
    }.issubset(tables)
    assert chapter_count == 1
    assert chapter.id == 1


def test_chapter_deletion_cascades_to_structure_versions_and_paragraph_maps(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    service = DissertationService(database)
    service.initialize()

    chapter = service.create_chapter("Unit A", 1)
    subchapter = service.create_subchapter(chapter.id, "Unit A.1", 1)
    chapter_version = service.record_document_version("chapter", chapter.id, SHA256, "manual")
    subchapter_version = service.record_document_version("subchapter", subchapter.id, "b" * 64, "manual")
    service.build_paragraph_map(chapter_version.id, 1)
    service.build_paragraph_map(subchapter_version.id, 1)

    with database.connection() as connection:
        connection.execute("DELETE FROM dissertation_chapters WHERE id = ?", (chapter.id,))
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "dissertation_subchapters",
                "dissertation_document_versions",
                "dissertation_paragraph_maps",
            )
        }

    assert counts == {
        "dissertation_subchapters": 0,
        "dissertation_document_versions": 0,
        "dissertation_paragraph_maps": 0,
    }


def test_chapter_deletion_does_not_remove_versions_of_a_same_id_subchapter(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    service = DissertationService(database)
    service.initialize()

    chapter_to_delete = service.create_chapter("Chapter A", 1)
    other_chapter = service.create_chapter("Chapter B", 2)
    colliding_subchapter = service.create_subchapter(other_chapter.id, "Sub of B", 1)
    assert colliding_subchapter.id == chapter_to_delete.id  # same numeric id, different target_type

    survivor = service.record_document_version("subchapter", colliding_subchapter.id, SHA256, "manual")
    service.record_document_version("chapter", chapter_to_delete.id, "b" * 64, "manual")

    with database.connection() as connection:
        connection.execute("DELETE FROM dissertation_chapters WHERE id = ?", (chapter_to_delete.id,))
        remaining = [
            dict(row) for row in connection.execute("SELECT * FROM dissertation_document_versions").fetchall()
        ]

    assert remaining == [
        {
            "id": survivor.id,
            "target_type": "subchapter",
            "target_id": colliding_subchapter.id,
            "content_hash": survivor.content_hash,
            "source": "manual",
            "version_state": "original",
            "created_at": survivor.created_at,
        }
    ]


def test_initialize_adds_version_state_to_a_preexisting_version_table(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    with database.connection() as connection:
        connection.executescript(
            """
            CREATE TABLE dissertation_document_versions (
                id INTEGER PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

    DissertationService(database).initialize()

    with database.connection() as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(dissertation_document_versions)").fetchall()
        }

    assert "version_state" in columns


def test_migration_preserves_legacy_rows_with_deterministic_default_and_stays_idempotent(
    tmp_path: Path,
) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    with database.connection() as connection:
        connection.executescript(
            """
            CREATE TABLE dissertation_document_versions (
                id INTEGER PRIMARY KEY,
                target_type TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO dissertation_document_versions "
            "(target_type, target_id, content_hash, source, created_at) VALUES "
            "('chapter', 1, ?, 'legacy', 't')",
            (SHA256,),
        )

    service = DissertationService(database)
    service.initialize()
    service.initialize()  # repeated initialization must not error or duplicate columns/triggers

    with database.connection() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM dissertation_document_versions").fetchall()]
        columns = [
            row["name"] for row in connection.execute("PRAGMA table_info(dissertation_document_versions)").fetchall()
        ]
        triggers = [row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall()]

    assert len(rows) == 1
    assert rows[0]["content_hash"] == SHA256
    assert rows[0]["version_state"] == "original"
    assert columns.count("version_state") == 1
    assert sorted(triggers) == [
        "delete_chapter_versions_before_chapter",
        "delete_subchapter_versions_before_subchapter",
    ]


def test_initialize_adds_sprint_6a_tables_and_columns(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    service = DissertationService(database)
    service.initialize()

    with database.connection() as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }

        chapter_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(dissertation_chapters)").fetchall()
        }

    expected_tables = {
        "dissertation_workspace",
        "dissertation_sources",
        "dissertation_source_chapter_links",
        "dissertation_evidence",
        "dissertation_notes",
        "dissertation_gaps",
        "dissertation_research_task_links",
        "dissertation_decision_links",
        "dissertation_research_audit_log"
    }

    assert expected_tables.issubset(tables)
    assert "current_focus" in chapter_columns


def test_fresh_evidence_table_has_confidence_column_with_medium_default(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    service = DissertationService(database)
    service.initialize()

    with database.connection() as connection:
        columns = {
            row["name"]: row["dflt_value"]
            for row in connection.execute("PRAGMA table_info(dissertation_evidence)").fetchall()
        }

    assert "confidence" in columns
    assert columns["confidence"] == "'MEDIUM'"


def test_initialize_adds_confidence_to_a_preexisting_evidence_table(tmp_path: Path) -> None:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    service = DissertationService(database)
    service.initialize()

    source = service.create_source("Legacy Source", "book", "Citation", "actor")

    # Simulate a pre-existing Sprint 6A evidence table that predates the confidence column.
    with database.connection() as connection:
        connection.execute("DROP TABLE dissertation_evidence")
        connection.executescript(
            """
            CREATE TABLE dissertation_evidence (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL,
                chapter_id INTEGER,
                gap_id INTEGER,
                summary TEXT NOT NULL,
                locator_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO dissertation_evidence "
            "(source_id, summary, created_at, updated_at) VALUES (?, ?, 't', 't')",
            (source.id, "Legacy summary"),
        )

    service.initialize()
    service.initialize()  # repeated initialization must not error or duplicate the column

    with database.connection() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM dissertation_evidence").fetchall()]
        columns = [
            row["name"] for row in connection.execute("PRAGMA table_info(dissertation_evidence)").fetchall()
        ]

    assert len(rows) == 1
    assert rows[0]["summary"] == "Legacy summary"
    assert rows[0]["source_id"] == source.id
    assert rows[0]["confidence"] == "MEDIUM"  # legacy row gets the safe default, not NULL or an error
    assert columns.count("confidence") == 1
