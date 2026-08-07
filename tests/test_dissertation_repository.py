"""Repository tests for Dissertation Workspace records and constraints."""

import sqlite3
from pathlib import Path

import pytest

from app.dissertation.repository import (
    DissertationRepository,
    InvalidDissertationValueError,
    InvalidReviewJobTransitionError,
)
from app.dissertation.service import DissertationService
from app.memory.database import MemoryDatabase

SHA256 = "a" * 64


@pytest.fixture
def repository(tmp_path: Path) -> DissertationRepository:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    DissertationService(database).initialize()
    return DissertationRepository(database)


def test_repository_crud_for_all_workspace_records(repository: DissertationRepository) -> None:
    chapter = repository.create_chapter("Unit B", 2)
    assert repository.update_chapter_status(chapter.id, "in_review").status == "in_review"

    subchapter = repository.create_subchapter(chapter.id, "Unit B.1", 1)
    assert repository.update_subchapter_status(subchapter.id, "revised").status == "revised"
    assert [item.id for item in repository.list_chapters()] == [chapter.id]
    assert [item.id for item in repository.list_subchapters(chapter.id)] == [subchapter.id]

    first_version = repository.create_document_version("chapter", chapter.id, SHA256, "manual")
    second_version = repository.create_document_version("chapter", chapter.id, "b" * 64, "manual")
    assert first_version.version_state == "original"
    assert repository.update_document_version_state(first_version.id, "working").version_state == "working"
    assert [item.id for item in repository.list_document_versions("chapter", chapter.id)] == [
        first_version.id,
        second_version.id,
    ]

    paragraph_map = repository.create_paragraph_map(first_version.id, 1, "stable-001")
    assert repository.list_paragraph_maps(first_version.id) == [paragraph_map]

    review_job = repository.create_review_job("subchapter", subchapter.id)
    assert repository.update_review_job_status(review_job.id, "in_progress", "Started").status == "in_progress"
    assert [item.id for item in repository.list_review_jobs("subchapter", subchapter.id)] == [review_job.id]

    log_entry = repository.append_revision_log("chapter", chapter.id, "system", "Structure updated")
    assert repository.list_revision_log("chapter", chapter.id) == [log_entry]


def test_review_job_allows_only_approved_transitions(repository: DissertationRepository) -> None:
    chapter = repository.create_chapter("Unit C", 1)
    review_job = repository.create_review_job("chapter", chapter.id)

    with pytest.raises(InvalidReviewJobTransitionError):
        repository.update_review_job_status(review_job.id, "completed", "Skipped")

    in_progress = repository.update_review_job_status(review_job.id, "in_progress", "Started")
    assert in_progress.status == "in_progress"
    assert repository.update_review_job_status(review_job.id, "failed", "Stopped").status == "failed"

    with pytest.raises(InvalidReviewJobTransitionError):
        repository.update_review_job_status(review_job.id, "in_progress", "Restart")


def test_review_job_transition_rejects_stale_expected_state(repository: DissertationRepository) -> None:
    chapter = repository.create_chapter("Unit E", 1)
    review_job = repository.create_review_job("chapter", chapter.id)
    repository.update_review_job_status(review_job.id, "in_progress", "Started")
    repository.update_review_job_status(review_job.id, "completed", "Finished")

    with repository.database.connection() as connection:
        cursor = connection.execute(
            "UPDATE dissertation_review_jobs SET status = ? WHERE id = ? AND status = ?",
            ("failed", review_job.id, "in_progress"),
        )
        assert cursor.rowcount == 0

    with pytest.raises(InvalidReviewJobTransitionError):
        repository.update_review_job_status(review_job.id, "failed", "Late")
    assert repository.get_review_job(review_job.id).status == "completed"


def test_document_version_state_transition_rejects_stale_expected_state(
    repository: DissertationRepository,
) -> None:
    chapter = repository.create_chapter("Unit E2", 1)
    version = repository.create_document_version("chapter", chapter.id, SHA256, "manual")
    repository.update_document_version_state(version.id, "working")
    repository.update_document_version_state(version.id, "reviewed")

    with repository.database.connection() as connection:
        cursor = connection.execute(
            "UPDATE dissertation_document_versions SET version_state = ? WHERE id = ? AND version_state = ?",
            ("approved", version.id, "working"),
        )
        assert cursor.rowcount == 0

    with pytest.raises(InvalidDissertationValueError):
        repository.update_document_version_state(version.id, "working")
    assert repository.get_document_version(version.id).version_state == "reviewed"


def test_database_constraints_reject_invalid_status_target_and_duplicate_ordinal(repository: DissertationRepository) -> None:
    chapter = repository.create_chapter("Unit D", 1)
    version = repository.create_document_version("chapter", chapter.id, SHA256, "manual")
    repository.create_paragraph_map(version.id, 1, "stable-001")

    with repository.database.connection() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE dissertation_chapters SET status = 'unknown' WHERE id = ?", (chapter.id,)
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO dissertation_document_versions "
                "(target_type, target_id, content_hash, source, created_at) VALUES ('unknown', ?, ?, ?, ?)",
                (chapter.id, SHA256, "manual", "2026-08-06T00:00:00Z"),
            )

    with pytest.raises(InvalidDissertationValueError):
        repository.create_paragraph_map(version.id, 1, "stable-002")

    with pytest.raises(InvalidDissertationValueError):
        repository.update_document_version_state(version.id, "approved")


def test_workspace_crud(repository: DissertationRepository) -> None:
    workspace = repository.get_or_create_workspace("My Diss", "CS")
    assert workspace.title == "My Diss"
    assert workspace.program == "CS"
    assert workspace.status == "active"

    updated = repository.update_workspace(status="writing", current_focus="Lit Review")
    assert updated.status == "writing"
    assert updated.current_focus == "Lit Review"

    # Idempotent create
    workspace2 = repository.get_or_create_workspace("Other", "Other")
    assert workspace2.id == workspace.id
    assert workspace2.title == "My Diss" # Should not overwrite

def test_source_and_evidence(repository: DissertationRepository) -> None:
    source = repository.create_source("Book A", "book", "Citation", None)
    assert source.title == "Book A"
    assert source.status == "unread"

    updated = repository.update_source_status(source.id, "reviewed")
    assert updated.status == "reviewed"

    chapter = repository.create_chapter("Ch 1", 1)
    repository.link_source_to_chapter(source.id, chapter.id)
    repository.link_source_to_chapter(source.id, chapter.id) # idempotent

    sources = repository.list_sources(chapter_id=chapter.id)
    assert len(sources) == 1
    assert sources[0].id == source.id

    evidence = repository.create_evidence(source.id, "Summary", chapter_id=chapter.id, gap_id=None, locator_detail="p. 1")
    assert evidence.summary == "Summary"

    ev_list = repository.list_evidence(chapter_id=chapter.id)
    assert len(ev_list) == 1

def test_gap_transitions(repository: DissertationRepository) -> None:
    gap = repository.create_gap("Missing info", "missing_evidence", chapter_id=None)
    assert gap.status == "open"

    updated = repository.update_gap_status(gap.id, "in_progress")
    assert updated.status == "in_progress"

    updated = repository.update_gap_status(gap.id, "resolved", resolution_note="Found it")
    assert updated.status == "resolved"
    assert updated.resolution_note == "Found it"
    assert updated.resolved_at is not None

    import pytest
    from app.dissertation.repository import InvalidGapTransitionError
    with pytest.raises(InvalidGapTransitionError):
        repository.update_gap_status(gap.id, "open") # Terminal

def test_note_creation(repository: DissertationRepository) -> None:
    chapter = repository.create_chapter("Ch 1", 1)
    note = repository.create_note("analysis", "My note", chapter_id=chapter.id)
    assert note.content == "My note"

    import pytest
    from app.dissertation.repository import InvalidDissertationValueError
    with pytest.raises(InvalidDissertationValueError):
        repository.create_note("analysis", "No relations")

def test_links_and_audit(repository: DissertationRepository) -> None:
    link = repository.create_research_task_link("wi_123", chapter_id=None, gap_id=None)
    assert link.work_item_id == "wi_123"

    d_link = repository.create_decision_link("dec_123", chapter_id=None)
    assert d_link.decision_id == "dec_123"

    repository.audit("source", 1, "created", "user1")

def test_fk_validations(repository: DissertationRepository) -> None:
    from app.dissertation.repository import DissertationTargetNotFoundError
    import pytest

    with pytest.raises(DissertationTargetNotFoundError, match="Source not found"):
        repository.create_evidence(999, "Summary", chapter_id=None, gap_id=None, locator_detail=None)

    source = repository.create_source("Source A", "book", "Citation", None)

    with pytest.raises(DissertationTargetNotFoundError, match="Chapter not found"):
        repository.create_evidence(source.id, "Summary", chapter_id=999, gap_id=None, locator_detail=None)

    with pytest.raises(DissertationTargetNotFoundError, match="Gap not found"):
        repository.create_evidence(source.id, "Summary", chapter_id=None, gap_id=999, locator_detail=None)

    with pytest.raises(DissertationTargetNotFoundError, match="Chapter not found"):
        repository.create_note("analysis", "Content", chapter_id=999)

    with pytest.raises(DissertationTargetNotFoundError, match="Source not found"):
        repository.create_note("analysis", "Content", source_id=999)

    with pytest.raises(DissertationTargetNotFoundError, match="Evidence not found"):
        repository.create_note("analysis", "Content", evidence_id=999)

    with pytest.raises(DissertationTargetNotFoundError, match="Gap not found"):
        repository.create_note("analysis", "Content", gap_id=999)
