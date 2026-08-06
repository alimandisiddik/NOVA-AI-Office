"""Domain models for NOVA Dissertation Workspace."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chapter:
    id: int
    title: str
    order_index: int
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Subchapter:
    id: int
    chapter_id: int
    title: str
    order_index: int
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class DocumentVersion:
    id: int
    target_type: str
    target_id: int
    content_hash: str
    source: str
    version_state: str
    created_at: str


@dataclass(frozen=True)
class ParagraphMap:
    id: int
    version_id: int
    paragraph_ordinal: int
    stable_paragraph_id: str
    created_at: str


@dataclass(frozen=True)
class ReviewJob:
    id: int
    target_type: str
    target_id: int
    status: str
    summary: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RevisionLogEntry:
    id: int
    target_type: str
    target_id: int
    actor: str
    reason: str
    created_at: str
