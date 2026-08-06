"""Parameterized SQLite repository for the Dissertation Workspace."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.dissertation.models import (
    Chapter,
    DocumentVersion,
    ParagraphMap,
    ReviewJob,
    RevisionLogEntry,
    Subchapter,
)
from app.memory.database import MemoryDatabase

CHAPTER_STATUSES = frozenset({"draft", "in_review", "revised", "final"})
TARGET_TYPES = frozenset({"chapter", "subchapter"})
REVIEW_JOB_STATUSES = frozenset({"queued", "in_progress", "completed", "failed"})
VERSION_STATES = frozenset({"original", "working", "reviewed", "approved"})
VERSION_STATE_TRANSITIONS = {
    "original": frozenset({"working"}),
    "working": frozenset({"reviewed"}),
    "reviewed": frozenset({"approved"}),
    "approved": frozenset(),
}
REVIEW_JOB_TRANSITIONS = {
    "queued": frozenset({"in_progress"}),
    "in_progress": frozenset({"completed", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
}


class DissertationError(RuntimeError):
    """Base error for safe Dissertation Workspace domain failures."""


class InvalidDissertationValueError(DissertationError):
    """Raised when a dissertation value is invalid."""


class DissertationTargetNotFoundError(DissertationError):
    """Raised when a chapter, subchapter, version, or job is not found."""


class InvalidReviewJobTransitionError(DissertationError):
    """Raised when a review-job state change is not permitted."""


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp compatible with Workspace Memory."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _chapter(row: sqlite3.Row) -> Chapter:
    return Chapter(**dict(row))


def _subchapter(row: sqlite3.Row) -> Subchapter:
    return Subchapter(**dict(row))


def _document_version(row: sqlite3.Row) -> DocumentVersion:
    return DocumentVersion(**dict(row))


def _paragraph_map(row: sqlite3.Row) -> ParagraphMap:
    return ParagraphMap(**dict(row))


def _review_job(row: sqlite3.Row) -> ReviewJob:
    return ReviewJob(**dict(row))


def _revision_log_entry(row: sqlite3.Row) -> RevisionLogEntry:
    return RevisionLogEntry(**dict(row))


class DissertationRepository:
    """Persist Dissertation Workspace records through parameterized SQL."""

    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def create_chapter(self, title: str, order_index: int) -> Chapter:
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO dissertation_chapters (title, order_index, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (title, order_index, now, now),
            )
            row = connection.execute(
                "SELECT * FROM dissertation_chapters WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return _chapter(row)

    def list_chapters(self) -> list[Chapter]:
        return self._many("SELECT * FROM dissertation_chapters ORDER BY order_index ASC, id ASC", (), _chapter)

    def update_chapter_status(self, chapter_id: int, status: str) -> Chapter:
        self._validate_chapter_status(status)
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "UPDATE dissertation_chapters SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, chapter_id),
            )
            if cursor.rowcount == 0:
                raise DissertationTargetNotFoundError("Chapter not found")
            row = connection.execute("SELECT * FROM dissertation_chapters WHERE id = ?", (chapter_id,)).fetchone()
        return _chapter(row)

    def create_subchapter(self, chapter_id: int, title: str, order_index: int) -> Subchapter:
        now = utc_now()
        self._require_target("chapter", chapter_id)
        with self.database.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO dissertation_subchapters "
                "(chapter_id, title, order_index, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (chapter_id, title, order_index, now, now),
            )
            row = connection.execute(
                "SELECT * FROM dissertation_subchapters WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return _subchapter(row)

    def list_subchapters(self, chapter_id: int) -> list[Subchapter]:
        return self._many(
            "SELECT * FROM dissertation_subchapters WHERE chapter_id = ? ORDER BY order_index ASC, id ASC",
            (chapter_id,),
            _subchapter,
        )

    def update_subchapter_status(self, subchapter_id: int, status: str) -> Subchapter:
        self._validate_chapter_status(status)
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "UPDATE dissertation_subchapters SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, subchapter_id),
            )
            if cursor.rowcount == 0:
                raise DissertationTargetNotFoundError("Subchapter not found")
            row = connection.execute(
                "SELECT * FROM dissertation_subchapters WHERE id = ?", (subchapter_id,)
            ).fetchone()
        return _subchapter(row)

    def create_document_version(
        self, target_type: str, target_id: int, content_hash: str, source: str
    ) -> DocumentVersion:
        self._require_target(target_type, target_id)
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO dissertation_document_versions "
                "(target_type, target_id, content_hash, source, version_state, created_at) "
                "VALUES (?, ?, ?, ?, 'original', ?)",
                (target_type, target_id, content_hash, source, now),
            )
            row = connection.execute(
                "SELECT * FROM dissertation_document_versions WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return _document_version(row)

    def update_document_version_state(self, version_id: int, version_state: str) -> DocumentVersion:
        if version_state not in VERSION_STATES:
            raise InvalidDissertationValueError("Invalid document version state")
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM dissertation_document_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row is None:
                raise DissertationTargetNotFoundError("Document version not found")
            current_state = row["version_state"]
            if version_state not in VERSION_STATE_TRANSITIONS[current_state]:
                raise InvalidDissertationValueError(
                    f"Cannot transition document version from {current_state} to {version_state}"
                )
            cursor = connection.execute(
                "UPDATE dissertation_document_versions SET version_state = ? "
                "WHERE id = ? AND version_state = ?",
                (version_state, version_id, current_state),
            )
            if cursor.rowcount == 0:
                raise InvalidDissertationValueError(
                    f"Document version {version_id} state changed concurrently; retry"
                )
            row = connection.execute(
                "SELECT * FROM dissertation_document_versions WHERE id = ?", (version_id,)
            ).fetchone()
        return _document_version(row)

    def get_document_version(self, version_id: int) -> DocumentVersion:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM dissertation_document_versions WHERE id = ?", (version_id,)
            ).fetchone()
        if row is None:
            raise DissertationTargetNotFoundError("Document version not found")
        return _document_version(row)

    def list_document_versions(self, target_type: str, target_id: int) -> list[DocumentVersion]:
        self._validate_target_type(target_type)
        return self._many(
            "SELECT * FROM dissertation_document_versions WHERE target_type = ? AND target_id = ? "
            "ORDER BY id ASC",
            (target_type, target_id),
            _document_version,
        )

    def create_paragraph_map(
        self, version_id: int, paragraph_ordinal: int, stable_paragraph_id: str
    ) -> ParagraphMap:
        now = utc_now()
        try:
            with self.database.connection() as connection:
                cursor = connection.execute(
                    "INSERT INTO dissertation_paragraph_maps "
                    "(version_id, paragraph_ordinal, stable_paragraph_id, created_at) VALUES (?, ?, ?, ?)",
                    (version_id, paragraph_ordinal, stable_paragraph_id, now),
                )
                row = connection.execute(
                    "SELECT * FROM dissertation_paragraph_maps WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
        except sqlite3.IntegrityError as error:
            if "UNIQUE constraint failed" in str(error):
                raise InvalidDissertationValueError("Paragraph ordinal already exists for version") from error
            raise DissertationTargetNotFoundError("Document version not found") from error
        return _paragraph_map(row)

    def list_paragraph_maps(self, version_id: int) -> list[ParagraphMap]:
        return self._many(
            "SELECT * FROM dissertation_paragraph_maps WHERE version_id = ? ORDER BY paragraph_ordinal ASC, id ASC",
            (version_id,),
            _paragraph_map,
        )

    def create_review_job(self, target_type: str, target_id: int) -> ReviewJob:
        self._require_target(target_type, target_id)
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO dissertation_review_jobs "
                "(target_type, target_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (target_type, target_id, now, now),
            )
            row = connection.execute(
                "SELECT * FROM dissertation_review_jobs WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return _review_job(row)

    def get_review_job(self, job_id: int) -> ReviewJob:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM dissertation_review_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise DissertationTargetNotFoundError("Review job not found")
        return _review_job(row)

    def update_review_job_status(self, job_id: int, status: str, summary: str) -> ReviewJob:
        if status not in REVIEW_JOB_STATUSES:
            raise InvalidDissertationValueError("Invalid review job status")
        now = utc_now()
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM dissertation_review_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise DissertationTargetNotFoundError("Review job not found")
            current_status = row["status"]
            if status not in REVIEW_JOB_TRANSITIONS[current_status]:
                raise InvalidReviewJobTransitionError(
                    f"Cannot transition review job from {current_status} to {status}"
                )
            cursor = connection.execute(
                "UPDATE dissertation_review_jobs SET status = ?, summary = ?, updated_at = ? "
                "WHERE id = ? AND status = ?",
                (status, summary, now, job_id, current_status),
            )
            if cursor.rowcount == 0:
                raise InvalidReviewJobTransitionError(
                    f"Review job {job_id} status changed concurrently; retry"
                )
            row = connection.execute("SELECT * FROM dissertation_review_jobs WHERE id = ?", (job_id,)).fetchone()
        return _review_job(row)

    def list_review_jobs(self, target_type: str, target_id: int) -> list[ReviewJob]:
        self._validate_target_type(target_type)
        return self._many(
            "SELECT * FROM dissertation_review_jobs WHERE target_type = ? AND target_id = ? ORDER BY id ASC",
            (target_type, target_id),
            _review_job,
        )

    def append_revision_log(
        self, target_type: str, target_id: int, actor: str, reason: str
    ) -> RevisionLogEntry:
        self._require_target(target_type, target_id)
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO dissertation_revision_log (target_type, target_id, actor, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (target_type, target_id, actor, reason, now),
            )
            row = connection.execute(
                "SELECT * FROM dissertation_revision_log WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return _revision_log_entry(row)

    def list_revision_log(self, target_type: str, target_id: int) -> list[RevisionLogEntry]:
        self._validate_target_type(target_type)
        return self._many(
            "SELECT * FROM dissertation_revision_log WHERE target_type = ? AND target_id = ? ORDER BY id ASC",
            (target_type, target_id),
            _revision_log_entry,
        )

    def _require_target(self, target_type: str, target_id: int) -> None:
        self._validate_target_type(target_type)
        with self.database.connection() as connection:
            if target_type == "chapter":
                row = connection.execute("SELECT 1 FROM dissertation_chapters WHERE id = ?", (target_id,)).fetchone()
            else:
                row = connection.execute(
                    "SELECT 1 FROM dissertation_subchapters WHERE id = ?", (target_id,)
                ).fetchone()
        if row is None:
            raise DissertationTargetNotFoundError(f"{target_type.capitalize()} not found")

    @staticmethod
    def _validate_target_type(target_type: str) -> None:
        if target_type not in TARGET_TYPES:
            raise InvalidDissertationValueError("Invalid dissertation target type")

    @staticmethod
    def _validate_chapter_status(status: str) -> None:
        if status not in CHAPTER_STATUSES:
            raise InvalidDissertationValueError("Invalid chapter status")

    def _many(self, query: str, parameters: tuple[object, ...], factory):
        with self.database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [factory(row) for row in rows]
