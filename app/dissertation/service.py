"""Validated service layer for the Dissertation Workspace."""

from __future__ import annotations

import re
import sqlite3
from uuid import NAMESPACE_URL, uuid5

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.control_tower.service import ControlTowerService

from app.dissertation.models import (
    Chapter,
    DocumentVersion,
    ParagraphMap,
    ReviewJob,
    RevisionLogEntry,
    Subchapter,
    DissertationWorkspace,
    Source,
    Evidence,
    ResearchNote,
    Gap,
    ResearchTaskLink,
    DecisionLink,
    DissertationOverview,
    ChapterDetail
)
from app.dissertation.repository import (
    DissertationRepository,
    InvalidDissertationValueError,
    DissertationTargetNotFoundError,
    DissertationConfigurationError,
)
from app.dissertation.schema import apply_schema
from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.security import SENSITIVE_CONTENT_PATTERN

SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class DissertationService:
    """Expose deterministic dissertation-workspace use cases."""

    def __init__(self, database: MemoryDatabase, *, control_tower: "ControlTowerService | None" = None) -> None:
        self.database = database
        self.repository = DissertationRepository(database)
        self.control_tower = control_tower

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

    def initialize_workspace(self, title: str, program: str, actor: str) -> DissertationWorkspace:
        title = self._required(title, "Workspace title")
        workspace = self.repository.get_or_create_workspace(title, program)
        self.repository.audit("workspace", workspace.id, "initialized", self._required(actor, "Actor"))
        return workspace

    def set_current_focus(self, current_focus: str, actor: str, *, chapter_id: int | None = None) -> None:
        focus = self._safe_required(current_focus, "Current focus")
        actor_clean = self._required(actor, "Actor")
        if chapter_id is None:
            workspace = self.repository.update_workspace(current_focus=focus)
            self.repository.audit("workspace", workspace.id, "focus_updated", actor_clean, focus)
        else:
            self.repository.update_chapter_focus(chapter_id, focus)

    def create_source(self, title: str, source_type: str, citation_text: str, actor: str, *, locator: str | None = None, chapter_id: int | None = None) -> Source:
        actor_clean = self._required(actor, "Actor")
        source = self.repository.create_source(
            self._required(title, "Source title"),
            source_type,
            self._safe_required(citation_text, "Citation text", max_length=2000),
            self._safe_optional(locator, max_length=500) if locator else None
        )
        if chapter_id is not None:
            self.repository.link_source_to_chapter(source.id, chapter_id)
        self.repository.audit("source", source.id, "created", actor_clean)
        return source

    def list_sources(self, *, chapter_id: int | None = None) -> list[Source]:
        return self.repository.list_sources(chapter_id=chapter_id)

    def create_evidence(
        self,
        source_id: int,
        summary: str,
        actor: str,
        *,
        chapter_id: int | None = None,
        gap_id: int | None = None,
        locator_detail: str | None = None,
        confidence: str = "MEDIUM",
    ) -> Evidence:
        actor_clean = self._required(actor, "Actor")
        evidence = self.repository.create_evidence(
            source_id,
            self._safe_required(summary, "Summary", max_length=1000),
            chapter_id=chapter_id,
            gap_id=gap_id,
            locator_detail=self._safe_optional(locator_detail, max_length=200) if locator_detail else None,
            confidence=confidence,
        )
        self.repository.audit("evidence", evidence.id, "created", actor_clean)
        return evidence

    def list_evidence(self, *, chapter_id: int | None = None, gap_id: int | None = None) -> list[Evidence]:
        return self.repository.list_evidence(chapter_id=chapter_id, gap_id=gap_id)

    def create_note(self, note_type: str, content: str, actor: str, *, chapter_id=None, source_id=None, evidence_id=None, gap_id=None) -> ResearchNote:
        actor_clean = self._required(actor, "Actor")
        note = self.repository.create_note(
            note_type,
            self._safe_required(content, "Content", max_length=4000),
            chapter_id=chapter_id,
            source_id=source_id,
            evidence_id=evidence_id,
            gap_id=gap_id
        )
        self.repository.audit("note", note.id, "created", actor_clean)
        return note

    def create_gap(self, description: str, gap_type: str, actor: str, *, chapter_id: int | None = None, priority: str = "normal") -> Gap:
        actor_clean = self._required(actor, "Actor")
        gap = self.repository.create_gap(
            self._safe_required(description, "Description", max_length=1000),
            gap_type,
            chapter_id=chapter_id,
            priority=priority
        )
        self.repository.audit("gap", gap.id, "created", actor_clean)
        return gap

    def resolve_gap(self, gap_id: int, resolution_note: str, actor: str) -> Gap:
        return self.update_gap_status(gap_id, "resolved", actor, resolution_note=resolution_note)

    def update_gap_status(self, gap_id: int, status: str, actor: str, *, resolution_note: str = "") -> Gap:
        actor_clean = self._required(actor, "Actor")
        res_note = self._safe_optional(resolution_note)
        gap = self.repository.update_gap_status(gap_id, status, resolution_note=res_note)
        self.repository.audit("gap", gap.id, f"status_updated_to_{status}", actor_clean, res_note)
        return gap

    def list_gaps(self, *, chapter_id: int | None = None, status: str | None = None) -> list[Gap]:
        return self.repository.list_gaps(chapter_id=chapter_id, status=status)

    def create_research_task(self, title: str, actor: str, *, chapter_id: int | None = None, gap_id: int | None = None, summary: str | None = None, urgency: int = 0, importance: int = 0) -> ResearchTaskLink:
        if self.control_tower is None:
            raise DissertationConfigurationError("Control Tower is required to create research tasks")

        actor_clean = self._required(actor, "Actor")

        work_item = self.control_tower.capture_work(
            "academic",
            self._required(title, "Research task title"),
            actor_clean,
            summary=self._safe_optional(summary) if summary else None,
            urgency=urgency,
            importance=importance,
        )

        link = self.repository.create_research_task_link(work_item.item_id, chapter_id=chapter_id, gap_id=gap_id)
        self.repository.audit("research_task_link", link.id, "created", actor_clean, work_item.item_id)
        return link

    def list_research_tasks(self, *, chapter_id: int | None = None) -> list[dict]:
        if self.control_tower is None:
            return []

        links = self.repository.list_research_task_links(chapter_id=chapter_id)
        results = []
        for link in links:
            work_item = self.control_tower.repository.get_work_item(link.work_item_id)
            if work_item is not None:
                results.append({
                    "link_id": link.id,
                    "chapter_id": link.chapter_id,
                    "gap_id": link.gap_id,
                    "work_item": work_item,
                })
        return results

    def create_decision_link(self, summary: str, rationale: str, impact: str, approved_by: str, actor: str, *, chapter_id: int | None = None) -> DecisionLink:
        if self.control_tower is None:
            raise DissertationConfigurationError("Control Tower is required to record decisions")

        actor_clean = self._required(actor, "Actor")

        decision = self.control_tower.register_decision(
            self._safe_required(summary, "Decision summary"),
            self._safe_required(rationale, "Decision rationale"),
            self._safe_required(impact, "Decision impact"),
            self._safe_required(approved_by, "Approved by"),
            actor_clean,
        )

        link = self.repository.create_decision_link(decision.decision_id, chapter_id=chapter_id)
        self.repository.audit("decision_link", link.id, "created", actor_clean, decision.decision_id)
        return link

    def list_decisions(self, *, chapter_id: int | None = None) -> list[dict]:
        if self.control_tower is None:
            return []

        links = self.repository.list_decision_links(chapter_id=chapter_id)
        results = []
        for link in links:
            decision = self.control_tower.repository.get_decision(link.decision_id)
            if decision is not None:
                results.append({
                    "link_id": link.id,
                    "chapter_id": link.chapter_id,
                    "decision": decision,
                })
        return results

    def get_overview(self) -> DissertationOverview:
        workspace = self.repository.get_workspace()
        if workspace is None:
            raise DissertationTargetNotFoundError("Workspace not initialized")

        chapters = self.repository.list_chapters()
        sources = self.repository.list_sources()
        evidence = self.repository.list_evidence()
        gaps = [g for g in self.repository.list_gaps() if g.status in {"open", "in_progress"}]
        tasks = [t for t in self.list_research_tasks() if getattr(t.get("work_item"), "status", "") not in {"completed", "cancelled"}]

        return DissertationOverview(
            workspace=workspace,
            chapters=chapters,
            chapter_progress={chapter.id: self._chapter_progress(chapter) for chapter in chapters},
            total_sources=len(sources),
            total_evidence=len(evidence),
            open_gaps=len(gaps),
            pending_tasks=len(tasks),
            current_focus=workspace.current_focus,
            next_action=self.get_next_action(),
            overall_progress=self._overall_progress(chapters),
        )

    def get_chapter_detail(self, chapter_id: int) -> ChapterDetail:
        chapters = self.repository.list_chapters()
        chapter = next((c for c in chapters if c.id == chapter_id), None)
        if chapter is None:
            raise InvalidDissertationValueError(f"Chapter {chapter_id} not found")

        subchapters = self.repository.list_subchapters(chapter_id)
        sources = self.repository.list_sources(chapter_id=chapter_id)
        evidence = self.repository.list_evidence(chapter_id=chapter_id)
        gaps = [g for g in self.repository.list_gaps(chapter_id=chapter_id) if g.status in {"open", "in_progress"}]
        tasks = [t for t in self.list_research_tasks(chapter_id=chapter_id) if getattr(t.get("work_item"), "status", "") not in {"completed", "cancelled"}]

        return ChapterDetail(
            chapter=chapter,
            subchapters=subchapters,
            sources=sources,
            evidence=evidence,
            open_gaps=len(gaps),
            pending_tasks=len(tasks),
            next_action=self.get_next_action(chapter_id=chapter_id),
            progress=self._chapter_progress(chapter),
        )

    def get_next_action(self, *, chapter_id: int | None = None) -> str:
        chapters = self.repository.list_chapters()
        scoped = [chapter for chapter in chapters if chapter_id is None or chapter.id == chapter_id]
        chapter_numbers = {chapter.id: chapter.order_index for chapter in scoped}

        critical_gaps = [
            gap for gap in self.repository.list_gaps(chapter_id=chapter_id)
            if gap.status == "open" and gap.priority == "critical"
        ]
        if critical_gaps:
            gap = critical_gaps[0]
            suffix = f" (Chapter {chapter_numbers[gap.chapter_id]})" if gap.chapter_id in chapter_numbers else ""
            return f"Resolve critical gap: {gap.description}{suffix}"

        pending_statuses = {"inbox", "clarification_needed", "planned", "in_progress", "awaiting_approval"}
        for task in self.list_research_tasks(chapter_id=chapter_id):
            work_item = task["work_item"]
            if work_item.status in pending_statuses:
                chapter_number = chapter_numbers.get(task["chapter_id"])
                suffix = f" (Chapter {chapter_number})" if chapter_number is not None else ""
                return f"Continue task: {work_item.title}{suffix}"

        open_gaps = [gap for gap in self.repository.list_gaps(chapter_id=chapter_id) if gap.status == "open"]
        if open_gaps:
            gap = open_gaps[0]
            suffix = f" (Chapter {chapter_numbers[gap.chapter_id]})" if gap.chapter_id in chapter_numbers else ""
            return f"Address open gap: {gap.description}{suffix}"

        for chapter in scoped:
            if chapter.status != "final":
                return f"Define next research task for Chapter {chapter.order_index} ({chapter.title})"
        if scoped:
            return "All chapters are final. Consider a full-dissertation review pass."
        return "No chapters defined yet. Create Chapter I to begin."

    def _chapter_progress(self, chapter: Chapter) -> float:
        weights = {"draft": 0, "in_review": 50, "revised": 75, "final": 100}
        subchapters = self.repository.list_subchapters(chapter.id)
        if not subchapters:
            return float(weights[chapter.status])
        return sum(weights[subchapter.status] for subchapter in subchapters) / len(subchapters)

    def _overall_progress(self, chapters: list[Chapter]) -> float:
        if not chapters:
            return 0.0
        return sum(self._chapter_progress(chapter) for chapter in chapters) / len(chapters)

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
    def _safe_required(cls, value: str, label: str, max_length: int | None = None) -> str:
        cleaned = cls._required(value, label)
        if max_length and len(cleaned) > max_length:
            raise InvalidDissertationValueError(f"{label} exceeds maximum length of {max_length}")
        cls._reject_sensitive_content(cleaned)
        return cleaned

    @classmethod
    def _safe_optional(cls, value: str, max_length: int | None = None) -> str:
        cleaned = value.strip()
        if max_length and len(cleaned) > max_length:
            raise InvalidDissertationValueError(f"Optional field exceeds maximum length of {max_length}")
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
