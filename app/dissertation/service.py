"""Validated service layer for the Dissertation Workspace."""

from __future__ import annotations

import re
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from app.dissertation.models import (
    Chapter,
    DocumentVersion,
    ParagraphMap,
    ReviewJob,
    RevisionLogEntry,
    Subchapter,
)
from app.dissertation.repository import DissertationRepository, InvalidDissertationValueError
from app.dissertation.schema import apply_schema
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.security import SENSITIVE_CONTENT_PATTERN

SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class DissertationService:
    """Expose deterministic dissertation-workspace use cases."""

    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database
        self.repository = DissertationRepository(database)

    def initialize(self) -> None:
        """Create the additive dissertation tables and cleanup triggers."""
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
        except sqlite3.Error as error:
            raise MemoryDatabaseError("Dissertation Workspace schema initialization failed") from error

    def create_chapter(self, title: str, order: int) -> Chapter:
        return self.repository.create_chapter(self._required(title, "Chapter title"), self._positive_order(order))

    def list_chapters(self) -> list[Chapter]:
        return self.repository.list_chapters()

    def update_chapter_status(self, chapter_id: int, status: str) -> Chapter:
        return self.repository.update_chapter_status(chapter_id, status)

    def create_subchapter(self, chapter_id: int, title: str, order: int) -> Subchapter:
        return self.repository.create_subchapter(
            chapter_id,
            self._required(title, "Subchapter title"),
            self._positive_order(order),
        )

    def list_subchapters(self, chapter_id: int) -> list[Subchapter]:
        return self.repository.list_subchapters(chapter_id)

    def update_subchapter_status(self, subchapter_id: int, status: str) -> Subchapter:
        return self.repository.update_subchapter_status(subchapter_id, status)

    def record_document_version(
        self, target_type: str, target_id: int, content_hash: str, source: str
    ) -> DocumentVersion:
        normalized_hash = self._sha256(content_hash)
        return self.repository.create_document_version(
            target_type,
            target_id,
            normalized_hash,
            self._required(source, "Document version source"),
        )

    def update_document_version_state(self, version_id: int, version_state: str) -> DocumentVersion:
        return self.repository.update_document_version_state(version_id, version_state)

    def build_paragraph_map(self, version_id: int, paragraph_count: int) -> list[ParagraphMap]:
        if isinstance(paragraph_count, bool) or not isinstance(paragraph_count, int) or paragraph_count < 0:
            raise InvalidDissertationValueError("Paragraph count must be a non-negative integer")
        self.repository.get_document_version(version_id)
        existing_maps = self.repository.list_paragraph_maps(version_id)
        if existing_maps:
            if len(existing_maps) != paragraph_count:
                raise InvalidDissertationValueError("Paragraph map already exists with a different paragraph count")
            return existing_maps
        return [
            self.repository.create_paragraph_map(
                version_id,
                paragraph_ordinal,
                str(uuid5(NAMESPACE_URL, f"nova-dissertation:{version_id}:{paragraph_ordinal}")),
            )
            for paragraph_ordinal in range(1, paragraph_count + 1)
        ]

    def create_review_job(self, target_type: str, target_id: int) -> ReviewJob:
        return self.repository.create_review_job(target_type, target_id)

    def update_review_job_status(self, job_id: int, status: str, summary: str = "") -> ReviewJob:
        return self.repository.update_review_job_status(job_id, status, self._safe_optional(summary))

    def append_revision_log(
        self, target_type: str, target_id: int, actor: str, reason: str
    ) -> RevisionLogEntry:
        return self.repository.append_revision_log(
            target_type,
            target_id,
            self._required(actor, "Revision log actor"),
            self._safe_required(reason, "Revision log reason"),
        )

    @staticmethod
    def _positive_order(order: int) -> int:
        if isinstance(order, bool) or not isinstance(order, int) or order < 1:
            raise InvalidDissertationValueError("Order must be a positive integer")
        return order

    @staticmethod
    def _required(value: str, label: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise InvalidDissertationValueError(f"{label} is required")
        return cleaned

    @classmethod
    def _safe_required(cls, value: str, label: str) -> str:
        cleaned = cls._required(value, label)
        cls._reject_sensitive_content(cleaned)
        return cleaned

    @classmethod
    def _safe_optional(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned:
            cls._reject_sensitive_content(cleaned)
        return cleaned

    @staticmethod
    def _sha256(content_hash: str) -> str:
        candidate = content_hash.strip()
        if not SHA256_PATTERN.fullmatch(candidate):
            raise InvalidDissertationValueError("Document version content_hash must be a SHA-256 hexadecimal digest")
        return candidate.lower()

    @staticmethod
    def _reject_sensitive_content(value: str) -> None:
        if SENSITIVE_CONTENT_PATTERN.search(value):
            raise InvalidDissertationValueError("Sensitive values cannot be stored in Dissertation Workspace")
