"""Validation and provenance orchestration for local Knowledge Operations."""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from app.knowledge.models import KnowledgeItem, KnowledgeResult, KnowledgeSource
from app.knowledge.repository import KnowledgeRepository
from app.knowledge.schema import apply_schema
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.memory.repositories import ProjectNotFoundError
from app.security import SENSITIVE_CONTENT_PATTERN

if TYPE_CHECKING:
    from app.control_tower.service import ControlTowerService
    from app.google_workspace.drive.models import DriveFileMetadata
    from app.memory.services import WorkspaceMemoryService


SOURCE_TYPES = frozenset({"document", "note", "drive_file", "calendar_event", "gmail_message", "manual", "conversation"})
ORIGIN_SYSTEMS = frozenset({"manual", "google_drive", "google_calendar", "dissertation", "telegram"})
CONFIDENCE_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})
MAX_TITLE_LENGTH = 500
MAX_CITATION_LENGTH = 2_000
MAX_SUMMARY_LENGTH = 1_000
MAX_TAGS_LENGTH = 500
MAX_ORIGIN_REF_LENGTH = 500
MAX_QUERY_LENGTH = 200

# Catches userinfo credentials even without a "scheme://" prefix (e.g.
# "user:pass@host/path"), which urlsplit() alone does not flag as having a
# netloc/username.
_ORIGIN_REF_USERINFO_PATTERN = re.compile(r"(?:^|//)[^/\s@]+:[^/\s@]+@")
# Common credential/token query-parameter names that must never ride along in
# an opaque origin_ref, independent of SENSITIVE_CONTENT_PATTERN's keyword set.
_ORIGIN_REF_CREDENTIAL_QUERY_PATTERN = re.compile(
    r"[?&](?:token|access_token|api[_-]?key|apikey|auth|password|passwd|pwd|"
    r"secret|session|sig|signature|jwt|credential)=",
    re.IGNORECASE,
)


class KnowledgeConfigurationError(RuntimeError):
    """Raised when a requested loose-reference check lacks its read seam."""


class InvalidKnowledgeValueError(ValueError):
    """Raised when Knowledge Operations input fails validation."""


class KnowledgeService:
    """Own Knowledge Operations writes while consuming cross-domain data read-only.

    Future boundary only, intentionally not implemented in Sprint 7B:
    ``register_source_from_drive(metadata: DriveFileMetadata, actor: str) -> KnowledgeSource``.
    It will accept Drive metadata only; it must not fetch file content or create an
    autonomous ingestion path.
    """

    def __init__(
        self,
        database: MemoryDatabase,
        *,
        memory: WorkspaceMemoryService | None = None,
        control_tower: ControlTowerService | None = None,
    ) -> None:
        self.database = database
        self.repository = KnowledgeRepository(database)
        self.memory = memory
        self.control_tower = control_tower

    def initialize(self) -> None:
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
        except sqlite3.Error as error:
            raise MemoryDatabaseError("Knowledge Operations schema initialization failed") from error

    def create_source(
        self,
        title: str,
        source_type: str,
        citation_text: str,
        actor: str,
        *,
        origin_system: str = "manual",
        origin_ref: str | None = None,
    ) -> KnowledgeSource:
        actor_clean = self._safe_required(actor, "Actor", 200)
        source_type_clean = self._enum(source_type, SOURCE_TYPES, "source type")
        origin_system_clean = self._enum(origin_system, ORIGIN_SYSTEMS, "origin system")
        source = self.repository.create_source(
            self._safe_required(title, "Title", MAX_TITLE_LENGTH),
            source_type_clean,
            self._safe_required(citation_text, "Citation text", MAX_CITATION_LENGTH),
            origin_system=origin_system_clean,
            origin_ref=self._opaque_origin_ref(origin_ref),
        )
        self.repository.audit("create", "knowledge_source", source.id, actor_clean)
        return source

    def create_item(
        self,
        source_id: int,
        title: str,
        summary: str,
        actor: str,
        *,
        tags: str = "",
        confidence: str = "MEDIUM",
        project_id: int | None = None,
        work_item_id: str | None = None,
    ) -> KnowledgeItem:
        actor_clean = self._safe_required(actor, "Actor", 200)
        source_id_clean = self._positive_id(source_id, "Source id")
        project_id_clean = self._optional_positive_id(project_id, "Project id")
        work_item_id_clean = self._safe_optional(work_item_id, "Work item id", 100)
        if project_id_clean is not None:
            self._ensure_project_exists(project_id_clean)
        if work_item_id_clean is not None:
            self._ensure_work_item_exists(work_item_id_clean)
        item = self.repository.create_item(
            source_id_clean,
            self._safe_required(title, "Title", MAX_TITLE_LENGTH),
            self._safe_required(summary, "Summary", MAX_SUMMARY_LENGTH),
            tags=self._safe_optional(tags, "Tags", MAX_TAGS_LENGTH) or "",
            confidence=self._enum(confidence, CONFIDENCE_LEVELS, "confidence"),
            project_id=project_id_clean,
            work_item_id=work_item_id_clean,
        )
        self.repository.audit("create", "knowledge_item", item.id, actor_clean, f"source:{item.source_id}")
        return item

    def query(
        self,
        keyword: str | None = None,
        tag: str | None = None,
        project_id: int | None = None,
    ) -> list[KnowledgeResult]:
        keyword_clean = self._safe_optional(keyword, "Keyword", MAX_QUERY_LENGTH)
        tag_clean = self._safe_optional(tag, "Tag", MAX_QUERY_LENGTH)
        project_id_clean = self._optional_positive_id(project_id, "Project id")
        return self.repository.query(keyword=keyword_clean, tag=tag_clean, project_id=project_id_clean)

    def _ensure_project_exists(self, project_id: int) -> None:
        if self.memory is None:
            raise KnowledgeConfigurationError("Workspace Memory is required to validate project references")
        try:
            self.memory.repository.get_project(project_id)
        except ProjectNotFoundError as error:
            raise InvalidKnowledgeValueError("Project not found") from error

    def _ensure_work_item_exists(self, work_item_id: str) -> None:
        if self.control_tower is None:
            raise KnowledgeConfigurationError("Control Tower is required to validate work item references")
        if self.control_tower.repository.get_work_item(work_item_id) is None:
            raise InvalidKnowledgeValueError("Work item not found")

    @staticmethod
    def _required(value: str, label: str) -> str:
        if not isinstance(value, str) or not (cleaned := value.strip()):
            raise InvalidKnowledgeValueError(f"{label} is required")
        return cleaned

    @classmethod
    def _safe_required(cls, value: str, label: str, max_length: int) -> str:
        cleaned = cls._required(value, label)
        if len(cleaned) > max_length:
            raise InvalidKnowledgeValueError(f"{label} exceeds maximum length of {max_length}")
        cls._reject_sensitive_content(cleaned)
        return cleaned

    @classmethod
    def _safe_optional(cls, value: str | None, label: str, max_length: int) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise InvalidKnowledgeValueError(f"{label} is invalid")
        cleaned = value.strip()
        if len(cleaned) > max_length:
            raise InvalidKnowledgeValueError(f"{label} exceeds maximum length of {max_length}")
        if cleaned:
            cls._reject_sensitive_content(cleaned)
        return cleaned or None

    @staticmethod
    def _positive_id(value: int, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise InvalidKnowledgeValueError(f"{label} must be a positive integer")
        return value

    @classmethod
    def _optional_positive_id(cls, value: int | None, label: str) -> int | None:
        return None if value is None else cls._positive_id(value, label)

    @staticmethod
    def _enum(value: str, allowed: frozenset[str], label: str) -> str:
        if not isinstance(value, str):
            raise InvalidKnowledgeValueError(f"Invalid {label}")
        candidate = value.strip()
        normalized = candidate.upper() if allowed is CONFIDENCE_LEVELS else candidate.lower()
        if normalized not in allowed:
            raise InvalidKnowledgeValueError(f"Invalid {label}")
        return normalized

    @staticmethod
    def _reject_sensitive_content(value: str) -> None:
        if SENSITIVE_CONTENT_PATTERN.search(value):
            raise InvalidKnowledgeValueError("Sensitive values cannot be stored in Knowledge Operations")

    @classmethod
    def _opaque_origin_ref(cls, value: str | None) -> str | None:
        cleaned = cls._safe_optional(value, "Origin reference", MAX_ORIGIN_REF_LENGTH)
        if cleaned is None:
            return None
        parsed = urlsplit(cleaned)
        has_userinfo = parsed.username is not None or parsed.password is not None
        if has_userinfo or _ORIGIN_REF_USERINFO_PATTERN.search(cleaned):
            raise InvalidKnowledgeValueError("Origin reference cannot contain URL credentials")
        if _ORIGIN_REF_CREDENTIAL_QUERY_PATTERN.search(cleaned):
            raise InvalidKnowledgeValueError("Origin reference cannot contain credential-bearing query parameters")
        return cleaned
