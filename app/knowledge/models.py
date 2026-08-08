"""Domain models for provenance-first local knowledge records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeSource:
    id: int
    title: str
    source_type: str
    origin_system: str
    origin_ref: str | None
    citation_text: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class KnowledgeItem:
    id: int
    source_id: int
    project_id: int | None
    work_item_id: str | None
    title: str
    summary: str
    tags: str
    confidence: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class KnowledgeResult:
    item: KnowledgeItem
    source: KnowledgeSource
