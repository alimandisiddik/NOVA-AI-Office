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
from app.dissertation.models import (
    DissertationWorkspace, Source, Evidence, ResearchNote, Gap, ResearchTaskLink, DecisionLink
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


class DissertationConfigurationError(DissertationError):
    """Raised when an operation requires a collaborator that was not injected (e.g. Control Tower)."""


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


WORKSPACE_STATUSES = frozenset({"planning","active","writing","revision","defense_ready","completed"})
GAP_STATUSES = frozenset({"open","in_progress","resolved","deferred"})
GAP_TRANSITIONS = {
    "open": frozenset({"in_progress", "deferred", "resolved"}),
    "in_progress": frozenset({"resolved", "deferred", "open"}),
    "deferred": frozenset({"open", "in_progress"}),
    "resolved": frozenset(),
}
SOURCE_TYPES = frozenset({"book","journal_article","thesis","conference_paper","report","website","dataset","other"})
NOTE_TYPES = frozenset({"analysis","synthesis","supervisor_feedback","question","other"})
GAP_TYPES = frozenset({"missing_evidence","conceptual_weakness","literature_gap","methodological_question","validation_needed","supervisor_feedback","other"})
AUDIT_TARGET_TYPES = frozenset({"workspace","source","evidence","note","gap","research_task_link","decision_link"})
class InvalidGapTransitionError(DissertationError):
    """Raised when a gap state change is not permitted."""
def _workspace(row: sqlite3.Row) -> DissertationWorkspace:
    return DissertationWorkspace(**dict(row))
def _source(row: sqlite3.Row) -> Source:
    return Source(**dict(row))
def _evidence(row: sqlite3.Row) -> Evidence:
    return Evidence(**dict(row))
def _note(row: sqlite3.Row) -> ResearchNote:
    return ResearchNote(**dict(row))
def _gap(row: sqlite3.Row) -> Gap:
    return Gap(**dict(row))
def _task_link(row: sqlite3.Row) -> ResearchTaskLink:
    return ResearchTaskLink(**dict(row))
def _decision_link(row: sqlite3.Row) -> DecisionLink:
    return DecisionLink(**dict(row))

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

    def get_workspace(self) -> DissertationWorkspace | None:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM dissertation_workspace WHERE id = 1").fetchone()
            if row is None:
                return None
            return _workspace(row)

    def get_or_create_workspace(self, title: str, program: str) -> DissertationWorkspace:
        now = utc_now()
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM dissertation_workspace WHERE id = 1").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO dissertation_workspace (id, title, program, created_at, updated_at) VALUES (1, ?, ?, ?, ?)",
                    (title, program, now, now),
                )
                row = connection.execute("SELECT * FROM dissertation_workspace WHERE id = 1").fetchone()
            return _workspace(row)
    def update_workspace(self, *, status: str | None = None, current_focus: str | None = None) -> DissertationWorkspace:
        now = utc_now()
        updates = []
        params = []
        if status is not None:
            if status not in WORKSPACE_STATUSES:
                raise InvalidDissertationValueError("Invalid workspace status")
            updates.append("status = ?")
            params.append(status)
        if current_focus is not None:
            updates.append("current_focus = ?")
            params.append(current_focus)

        if not updates:
            with self.database.connection() as connection:
                row = connection.execute("SELECT * FROM dissertation_workspace WHERE id = 1").fetchone()
                if row is None:
                    raise DissertationTargetNotFoundError("Workspace not initialized")
                return _workspace(row)

        updates.append("updated_at = ?")
        params.append(now)

        with self.database.connection() as connection:
            cursor = connection.execute(
                f"UPDATE dissertation_workspace SET {', '.join(updates)} WHERE id = 1",
                tuple(params),
            )
            if cursor.rowcount == 0:
                raise DissertationTargetNotFoundError("Workspace not initialized")
            row = connection.execute("SELECT * FROM dissertation_workspace WHERE id = 1").fetchone()
        return _workspace(row)
    def update_chapter_focus(self, chapter_id: int, current_focus: str) -> Chapter:
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "UPDATE dissertation_chapters SET current_focus = ?, updated_at = ? WHERE id = ?",
                (current_focus, now, chapter_id),
            )
            if cursor.rowcount == 0:
                raise DissertationTargetNotFoundError("Chapter not found")
            row = connection.execute("SELECT * FROM dissertation_chapters WHERE id = ?", (chapter_id,)).fetchone()
        return _chapter(row)
    def create_source(self, title: str, source_type: str, citation_text: str, locator: str | None) -> Source:
        if source_type not in SOURCE_TYPES:
            raise InvalidDissertationValueError("Invalid source type")
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO dissertation_sources (title, source_type, citation_text, locator, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (title, source_type, citation_text, locator, now, now),
            )
            row = connection.execute("SELECT * FROM dissertation_sources WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _source(row)
    def update_source_status(self, source_id: int, status: str) -> Source:
        if status not in {"unread", "reviewed", "cited", "rejected"}:
            raise InvalidDissertationValueError("Invalid source status")
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "UPDATE dissertation_sources SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, source_id),
            )
            if cursor.rowcount == 0:
                raise DissertationTargetNotFoundError("Source not found")
            row = connection.execute("SELECT * FROM dissertation_sources WHERE id = ?", (source_id,)).fetchone()
        return _source(row)
    def link_source_to_chapter(self, source_id: int, chapter_id: int) -> None:
        now = utc_now()
        with self.database.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO dissertation_source_chapter_links (source_id, chapter_id, created_at) VALUES (?, ?, ?)",
                (source_id, chapter_id, now)
            )
    def list_sources(self, *, chapter_id: int | None = None) -> list[Source]:
        if chapter_id is not None:
            return self._many(
                "SELECT s.* FROM dissertation_sources s JOIN dissertation_source_chapter_links l ON s.id = l.source_id WHERE l.chapter_id = ? ORDER BY s.id ASC",
                (chapter_id,),
                _source
            )
        return self._many("SELECT * FROM dissertation_sources ORDER BY id ASC", (), _source)
    def get_source(self, source_id: int) -> Source:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM dissertation_sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise DissertationTargetNotFoundError("Source not found")
        return _source(row)
    def create_evidence(self, source_id: int, summary: str, *, chapter_id: int | None, gap_id: int | None, locator_detail: str | None) -> Evidence:
        self.get_source(source_id) # ensure source exists
        if chapter_id is not None:
            self._require_target("chapter", chapter_id)
        if gap_id is not None:
            with self.database.connection() as connection:
                row = connection.execute("SELECT 1 FROM dissertation_gaps WHERE id = ?", (gap_id,)).fetchone()
            if row is None:
                raise DissertationTargetNotFoundError("Gap not found")

        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO dissertation_evidence (source_id, chapter_id, gap_id, summary, locator_detail, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source_id, chapter_id, gap_id, summary, locator_detail, now, now),
            )
            row = connection.execute("SELECT * FROM dissertation_evidence WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _evidence(row)
    def list_evidence(self, *, chapter_id: int | None = None, gap_id: int | None = None, source_id: int | None = None) -> list[Evidence]:
        query = "SELECT * FROM dissertation_evidence"
        conditions = []
        params = []
        if chapter_id is not None:
            conditions.append("chapter_id = ?")
            params.append(chapter_id)
        if gap_id is not None:
            conditions.append("gap_id = ?")
            params.append(gap_id)
        if source_id is not None:
            conditions.append("source_id = ?")
            params.append(source_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id ASC"

        return self._many(query, tuple(params), _evidence)
    def get_evidence(self, evidence_id: int) -> Evidence:
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM dissertation_evidence WHERE id = ?", (evidence_id,)).fetchone()
        if row is None:
            raise DissertationTargetNotFoundError("Evidence not found")
        return _evidence(row)
    def create_note(self, note_type: str, content: str, *, chapter_id=None, source_id=None, evidence_id=None, gap_id=None) -> ResearchNote:
        if note_type not in NOTE_TYPES:
            raise InvalidDissertationValueError("Invalid note type")
        if not any([chapter_id, source_id, evidence_id, gap_id]):
            raise InvalidDissertationValueError("Note requires at least one relation")

        with self.database.connection() as connection:
            if chapter_id is not None:
                self._require_target("chapter", chapter_id)
            if source_id is not None:
                row = connection.execute("SELECT 1 FROM dissertation_sources WHERE id = ?", (source_id,)).fetchone()
                if row is None:
                    raise DissertationTargetNotFoundError("Source not found")
            if evidence_id is not None:
                row = connection.execute("SELECT 1 FROM dissertation_evidence WHERE id = ?", (evidence_id,)).fetchone()
                if row is None:
                    raise DissertationTargetNotFoundError("Evidence not found")
            if gap_id is not None:
                row = connection.execute("SELECT 1 FROM dissertation_gaps WHERE id = ?", (gap_id,)).fetchone()
                if row is None:
                    raise DissertationTargetNotFoundError("Gap not found")

        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO dissertation_notes (chapter_id, source_id, evidence_id, gap_id, note_type, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chapter_id, source_id, evidence_id, gap_id, note_type, content, now, now),
            )
            row = connection.execute("SELECT * FROM dissertation_notes WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _note(row)
    def list_notes(self, *, chapter_id: int | None = None, gap_id: int | None = None) -> list[ResearchNote]:
        query = "SELECT * FROM dissertation_notes"
        conditions = []
        params = []
        if chapter_id is not None:
            conditions.append("chapter_id = ?")
            params.append(chapter_id)
        if gap_id is not None:
            conditions.append("gap_id = ?")
            params.append(gap_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id ASC"

        return self._many(query, tuple(params), _note)
    def create_gap(self, description: str, gap_type: str, *, chapter_id: int | None, priority: str = "normal") -> Gap:
        if gap_type not in GAP_TYPES:
            raise InvalidDissertationValueError("Invalid gap type")
        if priority not in {"low", "normal", "high", "critical"}:
            raise InvalidDissertationValueError("Invalid gap priority")

        if chapter_id is not None:
            self._require_target("chapter", chapter_id)

        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO dissertation_gaps (chapter_id, description, gap_type, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (chapter_id, description, gap_type, priority, now, now),
            )
            row = connection.execute("SELECT * FROM dissertation_gaps WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return _gap(row)
    def update_gap_status(self, gap_id: int, status: str, *, resolution_note: str = "") -> Gap:
        if status not in GAP_STATUSES:
            raise InvalidDissertationValueError("Invalid gap status")
        now = utc_now()
        with self.database.connection() as connection:
            row = connection.execute("SELECT * FROM dissertation_gaps WHERE id = ?", (gap_id,)).fetchone()
            if row is None:
                raise DissertationTargetNotFoundError("Gap not found")
            current_status = row["status"]
            if status not in GAP_TRANSITIONS[current_status]:
                raise InvalidGapTransitionError(f"Cannot transition gap from {current_status} to {status}")

            resolved_at = now if status == "resolved" else None

            cursor = connection.execute(
                "UPDATE dissertation_gaps SET status = ?, resolution_note = ?, resolved_at = ?, updated_at = ? WHERE id = ? AND status = ?",
                (status, resolution_note, resolved_at, now, gap_id, current_status),
            )
            if cursor.rowcount == 0:
                raise InvalidGapTransitionError(f"Gap {gap_id} status changed concurrently; retry")
            row = connection.execute("SELECT * FROM dissertation_gaps WHERE id = ?", (gap_id,)).fetchone()
        return _gap(row)
    def update_gap_next_action(self, gap_id: int, next_action: str) -> Gap:
        now = utc_now()
        with self.database.connection() as connection:
            cursor = connection.execute(
                "UPDATE dissertation_gaps SET next_action = ?, updated_at = ? WHERE id = ?",
                (next_action, now, gap_id)
            )
            if cursor.rowcount == 0:
                raise DissertationTargetNotFoundError("Gap not found")
            row = connection.execute("SELECT * FROM dissertation_gaps WHERE id = ?", (gap_id,)).fetchone()
        return _gap(row)
    def list_gaps(self, *, chapter_id: int | None = None, status: str | None = None) -> list[Gap]:
        query = "SELECT * FROM dissertation_gaps"
        conditions = []
        params = []
        if chapter_id is not None:
            conditions.append("chapter_id = ?")
            params.append(chapter_id)
        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id ASC"

        return self._many(query, tuple(params), _gap)
    def create_research_task_link(self, work_item_id: str, *, chapter_id: int | None, gap_id: int | None) -> ResearchTaskLink:
        now = utc_now()
        with self.database.connection() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO dissertation_research_task_links (chapter_id, gap_id, work_item_id, created_at) VALUES (?, ?, ?, ?)",
                    (chapter_id, gap_id, work_item_id, now),
                )
                row = connection.execute("SELECT * FROM dissertation_research_task_links WHERE id = ?", (cursor.lastrowid,)).fetchone()
            except sqlite3.IntegrityError:
                raise InvalidDissertationValueError("Research task link already exists for this work item")
        return _task_link(row)
    def list_research_task_links(self, *, chapter_id: int | None = None) -> list[ResearchTaskLink]:
        if chapter_id is not None:
            return self._many("SELECT * FROM dissertation_research_task_links WHERE chapter_id = ? ORDER BY id ASC", (chapter_id,), _task_link)
        return self._many("SELECT * FROM dissertation_research_task_links ORDER BY id ASC", (), _task_link)
    def create_decision_link(self, decision_id: str, *, chapter_id: int | None) -> DecisionLink:
        now = utc_now()
        with self.database.connection() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO dissertation_decision_links (chapter_id, decision_id, created_at) VALUES (?, ?, ?)",
                    (chapter_id, decision_id, now),
                )
                row = connection.execute("SELECT * FROM dissertation_decision_links WHERE id = ?", (cursor.lastrowid,)).fetchone()
            except sqlite3.IntegrityError:
                raise InvalidDissertationValueError("Decision link already exists for this decision")
        return _decision_link(row)
    def list_decision_links(self, *, chapter_id: int | None = None) -> list[DecisionLink]:
        if chapter_id is not None:
            return self._many("SELECT * FROM dissertation_decision_links WHERE chapter_id = ? ORDER BY id ASC", (chapter_id,), _decision_link)
        return self._many("SELECT * FROM dissertation_decision_links ORDER BY id ASC", (), _decision_link)
    def audit(self, target_type: str, target_id: int, event: str, actor: str, detail: str = "") -> None:
        if target_type not in AUDIT_TARGET_TYPES:
            raise InvalidDissertationValueError("Invalid audit target type")
        now = utc_now()
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO dissertation_research_audit_log (target_type, target_id, event, actor, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (target_type, target_id, event, actor, detail, now),
            )

    def _many(self, query: str, parameters: tuple[object, ...], factory):
        with self.database.connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [factory(row) for row in rows]
