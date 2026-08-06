"""Service tests for deterministic Dissertation Workspace behavior."""

from pathlib import Path

import pytest

from app.dissertation.repository import InvalidDissertationValueError, InvalidReviewJobTransitionError
from app.dissertation.service import DissertationService
from app.memory.database import MemoryDatabase

SHA256 = "A" * 64


@pytest.fixture
def service(tmp_path: Path) -> DissertationService:
    database = MemoryDatabase(tmp_path / "workspace.db")
    database.initialize()
    dissertation_service = DissertationService(database)
    dissertation_service.initialize()
    return dissertation_service


def test_chapter_and_subchapter_lists_are_ordered(service: DissertationService) -> None:
    later = service.create_chapter("Unit Later", 2)
    first = service.create_chapter("Unit First", 1)
    service.create_subchapter(first.id, "Unit First.2", 2)
    earliest_subchapter = service.create_subchapter(first.id, "Unit First.1", 1)

    assert [chapter.id for chapter in service.list_chapters()] == [first.id, later.id]
    assert [subchapter.id for subchapter in service.list_subchapters(first.id)] == [earliest_subchapter.id, 1]


def test_document_version_requires_sha256_metadata_only(service: DissertationService) -> None:
    chapter = service.create_chapter("Unit E", 1)

    version = service.record_document_version("chapter", chapter.id, SHA256, "manual")

    assert version.content_hash == SHA256.lower()
    assert service.update_document_version_state(version.id, "working").version_state == "working"
    assert service.update_document_version_state(version.id, "reviewed").version_state == "reviewed"
    assert service.update_document_version_state(version.id, "approved").version_state == "approved"
    with pytest.raises(InvalidDissertationValueError):
        service.record_document_version("chapter", chapter.id, "not-a-hash because it contains spaces", "manual")


def test_paragraph_map_is_deterministic_and_does_not_overwrite(service: DissertationService) -> None:
    chapter = service.create_chapter("Unit F", 1)
    version = service.record_document_version("chapter", chapter.id, SHA256, "manual")

    first_build = service.build_paragraph_map(version.id, 3)
    second_build = service.build_paragraph_map(version.id, 3)

    assert first_build == second_build
    assert [item.paragraph_ordinal for item in first_build] == [1, 2, 3]
    assert len({item.stable_paragraph_id for item in first_build}) == 3
    with pytest.raises(InvalidDissertationValueError):
        service.build_paragraph_map(version.id, 2)


def test_paragraph_map_rejects_negative_count_and_allows_empty_document(
    service: DissertationService,
) -> None:
    chapter = service.create_chapter("Unit F2", 1)
    version = service.record_document_version("chapter", chapter.id, SHA256, "manual")

    with pytest.raises(InvalidDissertationValueError):
        service.build_paragraph_map(version.id, -1)

    assert service.build_paragraph_map(version.id, 0) == []
    assert service.build_paragraph_map(version.id, 0) == []


def test_review_job_lifecycle_and_sensitive_log_guard(service: DissertationService) -> None:
    chapter = service.create_chapter("Unit G", 1)
    review_job = service.create_review_job("chapter", chapter.id)

    with pytest.raises(InvalidReviewJobTransitionError):
        service.update_review_job_status(review_job.id, "completed")
    with pytest.raises(InvalidDissertationValueError):
        service.update_review_job_status(review_job.id, "in_progress", "password value")

    assert service.update_review_job_status(review_job.id, "in_progress", "Started").status == "in_progress"
    assert service.update_review_job_status(review_job.id, "completed", "Finished").status == "completed"
    with pytest.raises(InvalidDissertationValueError):
        service.append_revision_log("chapter", chapter.id, "system", "api_key value")
