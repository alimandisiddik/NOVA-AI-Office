"""Typed records for manual external-message intake."""

from __future__ import annotations

from dataclasses import dataclass

SOURCE_CHANNEL_WHATSAPP_MANUAL = "whatsapp_manual"
CLASSIFICATION_LABELS = frozenset({"task", "note", "knowledge", "follow_up", "uncertain"})
INTAKE_STATUSES = frozenset({"pending_review", "confirmed", "dismissed"})
TARGET_TYPES = frozenset({"work_item", "note", "knowledge_item"})


@dataclass(frozen=True)
class IntakeClassification:
    """An explicitly labeled inference about a captured manual message."""

    label: str
    confidence: str
    suggested_fields: dict[str, str]


@dataclass(frozen=True)
class ExternalMessageIntake:
    """NOVA's canonical local identity for one manually supplied message."""

    id: int
    source_channel: str
    telegram_update_id: str | None
    raw_text: str
    classification: str
    status: str
    target_type: str | None
    target_id: str | None
    content_fingerprint: str
    created_by: str
    created_at: str
    updated_at: str
