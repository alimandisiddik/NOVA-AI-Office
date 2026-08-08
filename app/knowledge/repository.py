"""SQLite persistence for provenance-first knowledge records."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.knowledge.models import KnowledgeItem, KnowledgeResult, KnowledgeSource
from app.memory.database import MemoryDatabase


class KnowledgeError(RuntimeError):
    """Base error for local Knowledge Operations."""


class KnowledgeSourceNotFoundError(KnowledgeError):
    """Raised when an item is attached to an unknown source."""


def utc_now() -> str:
    """Return a deterministic, sortable UTC timestamp representation."""
    return datetime.now(UTC).isoformat()


def _source(row: sqlite3.Row) -> KnowledgeSource:
    return KnowledgeSource(
        id=row["id"], title=row["title"], source_type=row["source_type"],
        origin_system=row["origin_system"], origin_ref=row["origin_ref"],
        citation_text=row["citation_text"], status=row["status"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _item(row: sqlite3.Row) -> KnowledgeItem:
    return KnowledgeItem(
        id=row["id"], source_id=row["source_id"], project_id=row["project_id"],
        work_item_id=row["work_item_id"], title=row["title"], summary=row["summary"],
        tags=row["tags"], confidence=row["confidence"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


MAX_QUERY_RESULTS = 200


class KnowledgeRepository:
    """Persist only knowledge-owned tables in the shared local SQLite database."""

    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def create_source(
        self,
        title: str,
        source_type: str,
        citation_text: str,
        *,
        origin_system: str,
        origin_ref: str | None,
    ) -> KnowledgeSource:
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                """INSERT INTO knowledge_sources
                (title, source_type, origin_system, origin_ref, citation_text, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, source_type, origin_system, origin_ref, citation_text, now, now),
            )
            row = connection.execute("SELECT * FROM knowledge_sources WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _source(row)

    def get_source(self, source_id: int) -> KnowledgeSource | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM knowledge_sources WHERE id = ?", (source_id,)).fetchone()
        return _source(row) if row is not None else None

    def create_item(
        self,
        source_id: int,
        title: str,
        summary: str,
        *,
        tags: str,
        confidence: str,
        project_id: int | None,
        work_item_id: str | None,
    ) -> KnowledgeItem:
        if self.get_source(source_id) is None:
            raise KnowledgeSourceNotFoundError("Knowledge source not found")
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                """INSERT INTO knowledge_items
                (source_id, project_id, work_item_id, title, summary, tags, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_id, project_id, work_item_id, title, summary, tags, confidence, now, now),
            )
            row = connection.execute("SELECT * FROM knowledge_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _item(row)

    def query(
        self,
        *,
        keyword: str | None = None,
        tag: str | None = None,
        project_id: int | None = None,
    ) -> list[KnowledgeResult]:
        clauses: list[str] = []
        parameters: list[object] = []
        if keyword:
            clauses.append("(i.title LIKE ? ESCAPE '\\' OR i.summary LIKE ? ESCAPE '\\' OR i.tags LIKE ? ESCAPE '\\')")
            term = f"%{self._escape_like(keyword)}%"
            parameters.extend((term, term, term))
        if tag:
            clauses.append("(',' || lower(i.tags) || ',') LIKE ? ESCAPE '\\'")
            parameters.append(f"%,{self._escape_like(tag.lower())},%")
        if project_id is not None:
            clauses.append("i.project_id = ?")
            parameters.append(project_id)

        sql = """
            SELECT
                i.id AS item_id, i.source_id AS item_source_id, i.project_id AS item_project_id,
                i.work_item_id AS item_work_item_id, i.title AS item_title, i.summary AS item_summary,
                i.tags AS item_tags, i.confidence AS item_confidence, i.created_at AS item_created_at,
                i.updated_at AS item_updated_at,
                s.id AS source_id, s.title AS source_title, s.source_type AS source_type,
                s.origin_system AS source_origin_system, s.origin_ref AS source_origin_ref,
                s.citation_text AS source_citation_text, s.status AS source_status,
                s.created_at AS source_created_at, s.updated_at AS source_updated_at
            FROM knowledge_items i
            JOIN knowledge_sources s ON s.id = i.source_id
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY i.created_at DESC, i.id DESC LIMIT ?"
        parameters.append(MAX_QUERY_RESULTS)
        with self.database.connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [
            KnowledgeResult(
                item=KnowledgeItem(
                    id=row["item_id"], source_id=row["item_source_id"], project_id=row["item_project_id"],
                    work_item_id=row["item_work_item_id"], title=row["item_title"], summary=row["item_summary"],
                    tags=row["item_tags"], confidence=row["item_confidence"], created_at=row["item_created_at"],
                    updated_at=row["item_updated_at"],
                ),
                source=KnowledgeSource(
                    id=row["source_id"], title=row["source_title"], source_type=row["source_type"],
                    origin_system=row["source_origin_system"], origin_ref=row["source_origin_ref"],
                    citation_text=row["source_citation_text"], status=row["source_status"],
                    created_at=row["source_created_at"], updated_at=row["source_updated_at"],
                ),
            )
            for row in rows
        ]

    def audit(self, operation: str, entity_type: str, entity_id: int, actor: str, detail: str = "") -> None:
        with self.database.connection() as connection:
            connection.execute(
                """INSERT INTO knowledge_audit_log
                (operation, entity_type, entity_id, actor, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
                (operation, entity_type, str(entity_id), actor, detail, utc_now()),
            )

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
