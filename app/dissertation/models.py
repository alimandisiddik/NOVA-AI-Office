"""Domain models for NOVA Dissertation Workspace."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chapter:
    id: int
    title: str
    order_index: int
    status: str
    current_focus: str
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

@dataclass(frozen=True)
class DissertationWorkspace:
    id: int
    title: str
    program: str
    status: str
    current_focus: str
    created_at: str
    updated_at: str

@dataclass(frozen=True)
class Source:
    id: int
    title: str
    source_type: str
    citation_text: str
    locator: str | None
    status: str
    created_at: str
    updated_at: str

@dataclass(frozen=True)
class Evidence:
    id: int
    source_id: int
    chapter_id: int | None
    gap_id: int | None
    summary: str
    locator_detail: str | None
    created_at: str
    updated_at: str

@dataclass(frozen=True)
class ResearchNote:
    id: int
    chapter_id: int | None
    source_id: int | None
    evidence_id: int | None
    gap_id: int | None
    note_type: str
    content: str
    created_at: str
    updated_at: str

@dataclass(frozen=True)
class Gap:
    id: int
    chapter_id: int | None
    description: str
    gap_type: str
    status: str
    priority: str
    next_action: str
    resolution_note: str
    resolved_at: str | None
    created_at: str
    updated_at: str

@dataclass(frozen=True)
class ResearchTaskLink:
    id: int
    chapter_id: int | None
    gap_id: int | None
    work_item_id: str
    created_at: str

@dataclass(frozen=True)
class DecisionLink:
    id: int
    chapter_id: int | None
    decision_id: str
    created_at: str

@dataclass(frozen=True)
class DissertationOverview:
    workspace: DissertationWorkspace
    chapters: list[Chapter]
    chapter_progress: dict[int, float]
    total_sources: int
    total_evidence: int
    open_gaps: int
    pending_tasks: int
    current_focus: str
    next_action: str
    overall_progress: float

@dataclass(frozen=True)
class ChapterDetail:
    chapter: Chapter
    subchapters: list[Subchapter]
    sources: list[Source]
    evidence: list[Evidence]
    open_gaps: int
    pending_tasks: int
    next_action: str
    progress: float
