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
