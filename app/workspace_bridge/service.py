"""Explicit candidate-to-canonical-service promotion for Workspace signals."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any, Protocol

from app.control_tower.service import ControlTowerService
from app.google_workspace.calendar.dtos import MeetingBrief
from app.google_workspace.drive.models import DriveFileMetadata
from app.google_workspace.gmail.dtos import MessageSummary
from app.knowledge.service import KnowledgeService
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.security import SENSITIVE_CONTENT_PATTERN
from app.workspace_bridge.models import WorkspaceSourceRef
from app.workspace_bridge.repository import WorkspaceBridgeRepository, WorkspaceSourceRefNotFoundError
from app.workspace_bridge.schema import apply_schema

_MAX_SUMMARY_LENGTH = 1_000
_TARGET_TYPES = frozenset({"work_item", "decision", "knowledge_item"})
_DEADLINE_PATTERN = re.compile(r"\b(?:by|before|due)\s+[^.\n]{1,80}", re.IGNORECASE)
_IMPERATIVE_PATTERN = re.compile(r"\b(?:please|review|prepare|send|reply|approve|complete|schedule)\b", re.IGNORECASE)


class _Authenticator(Protocol):
    def get_account_namespace(self) -> str | None: ...


class WorkspaceBridgeError(RuntimeError):
    """Base class for sanitized bridge failures."""


class WorkspaceAccountUnavailableError(WorkspaceBridgeError):
    """Raised when account provenance cannot be established safely."""


class WorkspaceBridgeValidationError(WorkspaceBridgeError):
    """Raised when a source DTO cannot provide stable, safe provenance."""


class WorkspaceBridgeSensitiveContentError(WorkspaceBridgeError):
    """Raised before sensitive candidate text reaches persistence."""


class WorkspaceBridgeStaleStateError(WorkspaceBridgeError):
    """Raised when a terminal candidate is committed or dismissed again."""


class WorkspaceBridgeService:
    """Create review-only candidates and promote them only on explicit commit."""

    def __init__(
        self,
        database: MemoryDatabase,
        authenticator: _Authenticator,
        control_tower: ControlTowerService,
        knowledge: KnowledgeService,
    ) -> None:
        self.database = database
        self.authenticator = authenticator
        self.control_tower = control_tower
        self.knowledge = knowledge
        self.repository = WorkspaceBridgeRepository(database)

    def initialize(self) -> None:
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
        except sqlite3.Error as error:
            raise MemoryDatabaseError("Workspace bridge schema initialization failed") from error

    def propose_from_message(self, message: MessageSummary, actor: str) -> WorkspaceSourceRef:
        source_id = self._required_source_id(message.message_id, "message")
        summary = self._message_summary(message)
        return self._propose("gmail", "message", source_id, summary, actor)

    def propose_from_meeting(self, brief: MeetingBrief, actor: str) -> WorkspaceSourceRef:
        event_id = getattr(brief, "event_id", None)
        source_id = self._required_source_id(event_id, "event")
        summary = self._bounded(f"Meeting candidate (inference): review {brief.title}")
        return self._propose("calendar", "event", source_id, summary, actor)

    def propose_from_document(self, metadata: DriveFileMetadata | Any, actor: str) -> WorkspaceSourceRef:
        source_id = self._required_source_id(getattr(metadata, "file_id", None), "file")
        title = getattr(metadata, "name", None) or getattr(metadata, "title", None)
        if not isinstance(title, str) or not title.strip():
            raise WorkspaceBridgeValidationError("Workspace document title is unavailable")
        summary = self._bounded(f"Document candidate (inference): preserve knowledge from {title.strip()}")
        return self._propose("drive", "file", source_id, summary, actor)

    def commit(self, ref_id: int, target_type: str, actor: str, **fields: Any) -> WorkspaceSourceRef:
        ref = self._candidate(ref_id)
        if target_type not in _TARGET_TYPES:
            raise WorkspaceBridgeValidationError("Workspace target type is invalid")
        self._assert_safe(ref.candidate_summary)
        try:
            if target_type == "work_item":
                created_id = self._commit_work_item(ref, actor, fields)
            elif target_type == "decision":
                created_id = self._commit_decision(ref, actor, fields)
            else:
                created_id = self._commit_knowledge_item(ref, actor, fields)
            return self.repository.mark_committed(ref.id, target_type, created_id, actor)
        except WorkspaceBridgeError:
            raise
        except Exception:
            raise WorkspaceBridgeError("Workspace candidate could not be committed") from None

    def dismiss(self, ref_id: int, actor: str, reason: str) -> WorkspaceSourceRef:
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            raise WorkspaceBridgeValidationError("Dismissal reason is invalid")
        self._assert_safe(reason)
        self._candidate(ref_id)
        try:
            return self.repository.mark_dismissed(ref_id, actor, reason.strip())
        except (WorkspaceSourceRefNotFoundError, RuntimeError) as error:
            raise WorkspaceBridgeStaleStateError("Workspace candidate is no longer pending") from error

    def list_candidates(self) -> list[WorkspaceSourceRef]:
        return self.repository.list_candidates()

    def _propose(
        self,
        source_system: str,
        source_type: str,
        source_id: str,
        summary: str,
        actor: str,
    ) -> WorkspaceSourceRef:
        self._assert_actor(actor)
        self._assert_safe(summary)
        namespace = self.authenticator.get_account_namespace()
        if not isinstance(namespace, str) or not namespace.strip():
            raise WorkspaceAccountUnavailableError("Workspace account identity is unavailable")
        return self.repository.create_or_get(
            source_system=source_system,
            account_namespace=namespace.strip(),
            external_source_type=source_type,
            external_source_id=source_id,
            content_fingerprint=hashlib.sha256(self._normalized(summary).encode("utf-8")).hexdigest(),
            candidate_summary=summary,
            actor=actor.strip(),
        )

    def _candidate(self, ref_id: int) -> WorkspaceSourceRef:
        if isinstance(ref_id, bool) or not isinstance(ref_id, int) or ref_id <= 0:
            raise WorkspaceBridgeValidationError("Workspace candidate id is invalid")
        ref = self.repository.get(ref_id)
        if ref is None:
            raise WorkspaceBridgeValidationError("Workspace candidate was not found")
        if ref.status != "candidate":
            raise WorkspaceBridgeStaleStateError("Workspace candidate is no longer pending")
        return ref

    def _commit_work_item(self, ref: WorkspaceSourceRef, actor: str, fields: dict[str, Any]) -> str:
        title = self._field_text(fields, "title", ref.candidate_summary, 500)
        item = self.control_tower.capture_work(
            self._field_text(fields, "category", "workspace", 100),
            title,
            actor,
            summary=self._field_text(fields, "summary", ref.candidate_summary, _MAX_SUMMARY_LENGTH),
            urgency=self._field_int(fields, "urgency", 0),
            importance=self._field_int(fields, "importance", 0),
            deadline=fields.get("deadline"),
            clarification_needs="Workspace-derived candidate; source provenance is retained in Workspace bridge reference.",
            correlation_id=f"workspace_source_ref:{ref.id}",
        )
        return item.item_id

    def _commit_decision(self, ref: WorkspaceSourceRef, actor: str, fields: dict[str, Any]) -> str:
        decision = self.control_tower.register_decision(
            self._field_text(fields, "summary", ref.candidate_summary, 500),
            self._field_text(fields, "rationale", "Workspace-derived signal; explicit user confirmation recorded.", 1_000),
            self._field_text(fields, "impact", "Requires review in NOVA Control Tower.", 1_000),
            self._field_text(fields, "approved_by", actor, 200),
            actor,
            correlation_id=f"workspace_source_ref:{ref.id}",
        )
        return decision.decision_id

    def _commit_knowledge_item(self, ref: WorkspaceSourceRef, actor: str, fields: dict[str, Any]) -> str:
        source_type = {"gmail": "gmail_message", "calendar": "calendar_event", "drive": "drive_file"}[ref.source_system]
        source = self.knowledge.create_source(
            self._field_text(fields, "title", ref.candidate_summary, 500),
            source_type,
            self._field_text(fields, "citation_text", ref.candidate_summary, _MAX_SUMMARY_LENGTH),
            actor,
            origin_ref=f"workspace_source_ref:{ref.id}",
        )
        item = self.knowledge.create_item(
            source.id,
            self._field_text(fields, "title", ref.candidate_summary, 500),
            self._field_text(fields, "summary", ref.candidate_summary, _MAX_SUMMARY_LENGTH),
            actor,
            confidence="LOW",
        )
        return str(item.id)

    @staticmethod
    def _required_source_id(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise WorkspaceBridgeValidationError(f"Workspace {label} identity is unavailable")
        return value.strip()

    @staticmethod
    def _assert_actor(actor: str) -> None:
        if not isinstance(actor, str) or not actor.strip() or len(actor.strip()) > 200:
            raise WorkspaceBridgeValidationError("Workspace actor is invalid")

    @staticmethod
    def _normalized(value: str) -> str:
        return " ".join(value.lower().split())

    @classmethod
    def _bounded(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise WorkspaceBridgeValidationError("Workspace candidate summary is unavailable")
        return cleaned[:_MAX_SUMMARY_LENGTH]

    @staticmethod
    def _assert_safe(value: str) -> None:
        if SENSITIVE_CONTENT_PATTERN.search(value):
            raise WorkspaceBridgeSensitiveContentError("Workspace candidate contains sensitive content")

    @classmethod
    def _field_text(cls, fields: dict[str, Any], key: str, default: str, limit: int) -> str:
        value = fields.get(key, default)
        if not isinstance(value, str) or not value.strip():
            raise WorkspaceBridgeValidationError(f"Workspace {key} is invalid")
        cleaned = cls._bounded(value)
        if len(cleaned) > limit:
            raise WorkspaceBridgeValidationError(f"Workspace {key} is too long")
        cls._assert_safe(cleaned)
        return cleaned

    @staticmethod
    def _field_int(fields: dict[str, Any], key: str, default: int) -> int:
        value = fields.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
            raise WorkspaceBridgeValidationError(f"Workspace {key} is invalid")
        return value

    @classmethod
    def _message_summary(cls, message: MessageSummary) -> str:
        text = f"{message.subject} {message.snippet}".strip()
        if _DEADLINE_PATTERN.search(text):
            kind = "work item with a possible deadline"
        elif _IMPERATIVE_PATTERN.search(text):
            kind = "work item"
        else:
            kind = "knowledge item"
        return cls._bounded(f"Email candidate (inference): {kind} — {message.subject}")
