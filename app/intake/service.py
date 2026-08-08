"""Capture, classify, and explicitly promote manual external messages."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from app.intake.models import (
    CLASSIFICATION_LABELS,
    SOURCE_CHANNEL_WHATSAPP_MANUAL,
    TARGET_TYPES,
    ExternalMessageIntake,
    IntakeClassification,
)
from app.intake.repository import IntakeNotFoundError, IntakeRepository
from app.intake.schema import apply_schema
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.security import SENSITIVE_CONTENT_PATTERN

if TYPE_CHECKING:
    from app.control_tower.service import ControlTowerService
    from app.knowledge.service import KnowledgeService
    from app.memory.services import WorkspaceMemoryService


MAX_RAW_TEXT_LENGTH = 4_000
DEDUPLICATION_WINDOW = timedelta(minutes=5)
_WHITESPACE_PATTERN = re.compile(r"\s+")


class IntakeError(ValueError):
    """Raised for invalid manual-intake input or state transitions."""


class IntakeSensitiveContentError(IntakeError):
    """Raised before sensitive content can reach intake persistence."""


class IntakeStaleStateError(IntakeError):
    """Raised when a terminal intake is confirmed or dismissed again."""


class LLMClassifier(Protocol):
    def __call__(self, raw_text: str) -> IntakeClassification | None: ...


class IntakeService:
    """Own local intake rows while delegating confirmed writes to public seams."""

    def __init__(
        self,
        database: MemoryDatabase,
        *,
        memory: WorkspaceMemoryService | None = None,
        control_tower: ControlTowerService | None = None,
        knowledge: KnowledgeService | None = None,
        llm_classifier: LLMClassifier | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.repository = IntakeRepository(database)
        self.memory = memory
        self.control_tower = control_tower
        self.knowledge = knowledge
        self.llm_classifier = llm_classifier
        self.clock = clock or (lambda: datetime.now(UTC))

    def initialize(self) -> None:
        try:
            with self.database.connection() as connection:
                apply_schema(connection)
        except sqlite3.Error as error:
            raise MemoryDatabaseError("External message intake schema initialization failed") from error

    def capture(
        self,
        source_channel: str,
        raw_text: str,
        actor: str,
        telegram_update_id: str | None = None,
    ) -> ExternalMessageIntake:
        source = self._source_channel(source_channel)
        text = self._safe_raw_text(raw_text)
        actor_clean = self._required(actor, "Actor", 200)
        update_id = self._optional_update_id(telegram_update_id)
        if update_id is not None:
            existing = self.repository.find_by_telegram_update_id(update_id)
            if existing is not None:
                return existing

        fingerprint = self._fingerprint(text)
        now = self._now()
        duplicate = self.repository.find_recent_pending_by_fingerprint(
            fingerprint, (now - DEDUPLICATION_WINDOW).isoformat()
        )
        if duplicate is not None:
            return duplicate
        classification = self.classify(text)
        return self.repository.create(
            source_channel=source,
            telegram_update_id=update_id,
            raw_text=text,
            classification=classification.label,
            content_fingerprint=fingerprint,
            created_by=actor_clean,
            created_at=now.isoformat(),
        )

    def classify(self, raw_text: str) -> IntakeClassification:
        """Classify with deterministic markers before optional LLM assistance."""
        text = self._safe_raw_text(raw_text)
        lower = text.lower()
        for prefix, label in (("task:", "task"), ("note:", "note"), ("knowledge:", "knowledge"), ("follow up:", "follow_up"), ("follow-up:", "follow_up")):
            if lower.startswith(prefix):
                return IntakeClassification(label, "deterministic", {"title": text[len(prefix):].strip() or text})
        if lower.startswith(("fyi:", "for your information:")):
            return IntakeClassification("knowledge", "deterministic", {"title": text})
        if any(marker in lower for marker in ("please follow up", "please follow-up", "follow up with", "follow-up with")):
            return IntakeClassification("follow_up", "deterministic", {"title": text})

        uncertain = IntakeClassification("uncertain", "uncertain", {})
        if self.llm_classifier is None:
            return uncertain
        result = self.llm_classifier(text)
        if result is None or result.label not in CLASSIFICATION_LABELS or result.label == "uncertain":
            return uncertain
        return IntakeClassification(result.label, "llm_assisted", dict(result.suggested_fields))

    def confirm(self, intake_id: int, target_type: str, actor: str, **fields: str) -> ExternalMessageIntake:
        intake = self._pending(intake_id)
        actor_clean = self._required(actor, "Actor", 200)
        if target_type not in TARGET_TYPES:
            raise IntakeError("Unsupported intake target type")

        try:
            if target_type == "work_item":
                if self.control_tower is None:
                    raise IntakeError("Control Tower is unavailable")
                item = self.control_tower.capture_work(
                    fields.get("category", "administrative"),
                    fields.get("title", intake.raw_text),
                    actor_clean,
                    summary=fields.get("summary", intake.raw_text),
                )
                target_id = item.item_id
            elif target_type == "note":
                if self.memory is None:
                    raise IntakeError("Workspace Memory is unavailable")
                note = self.memory.create_note(
                    self._required(fields.get("project_name", ""), "Project name", 500),
                    fields.get("content", intake.raw_text),
                )
                target_id = str(note.id)
            else:
                if self.knowledge is None:
                    raise IntakeError("Knowledge Operations is unavailable")
                source = self.knowledge.create_source(
                    fields.get("source_title", fields.get("title", "Manual external message")),
                    "manual",
                    fields.get("citation_text", f"Manual external-message intake #{intake.id}"),
                    actor_clean,
                    origin_system="telegram",
                    origin_ref=f"intake:{intake.id}",
                )
                item = self.knowledge.create_item(
                    source.id,
                    fields.get("title", intake.raw_text),
                    fields.get("summary", intake.raw_text),
                    actor_clean,
                    tags=fields.get("tags", ""),
                    confidence=fields.get("confidence", "MEDIUM"),
                )
                target_id = str(item.id)
        except IntakeError:
            raise
        except (RuntimeError, ValueError) as error:
            raise IntakeError("Promotion was rejected by the target service; no record was created") from error
        return self.repository.resolve(
            intake.id, target_type=target_type, target_id=target_id, actor=actor_clean,
            updated_at=self._now().isoformat(),
        )

    def dismiss(self, intake_id: int, actor: str, reason: str) -> ExternalMessageIntake:
        self._pending(intake_id)
        return self.repository.dismiss(
            intake_id,
            actor=self._required(actor, "Actor", 200),
            reason=self._required(reason, "Dismissal reason", 500),
            updated_at=self._now().isoformat(),
        )

    def _pending(self, intake_id: int) -> ExternalMessageIntake:
        try:
            intake = self.repository.get(intake_id)
        except (IntakeNotFoundError, ValueError) as error:
            raise IntakeError("External message intake was not found") from error
        if intake.status != "pending_review":
            raise IntakeStaleStateError("External message intake is no longer pending")
        return intake

    @staticmethod
    def _source_channel(value: str) -> str:
        if value != SOURCE_CHANNEL_WHATSAPP_MANUAL:
            raise IntakeError("Unsupported external message source")
        return value

    @staticmethod
    def _required(value: str, label: str, maximum: int) -> str:
        if not isinstance(value, str) or not (cleaned := value.strip()):
            raise IntakeError(f"{label} is required")
        if len(cleaned) > maximum:
            raise IntakeError(f"{label} is too long")
        return cleaned

    @classmethod
    def _safe_raw_text(cls, value: str) -> str:
        cleaned = cls._required(value, "Message text", MAX_RAW_TEXT_LENGTH)
        if SENSITIVE_CONTENT_PATTERN.search(cleaned):
            raise IntakeSensitiveContentError("Sensitive content cannot be captured")
        return cleaned

    @staticmethod
    def _optional_update_id(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not (cleaned := value.strip()) or len(cleaned) > 200:
            raise IntakeError("Telegram update identifier is invalid")
        return cleaned

    @staticmethod
    def _fingerprint(value: str) -> str:
        normalized = _WHITESPACE_PATTERN.sub(" ", value.strip()).casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _now(self) -> datetime:
        timestamp = self.clock()
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=UTC)
        return timestamp.astimezone(UTC)
