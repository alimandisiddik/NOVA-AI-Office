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


def test_workspace_initialization(service: DissertationService) -> None:
    workspace = service.initialize_workspace("My Diss", "CS", "actor1")
    assert workspace.title == "My Diss"
    assert workspace.program == "CS"
    assert workspace.status == "active"

    service.set_current_focus("Lit Review", "actor1")
    assert service.repository.get_or_create_workspace("My Diss", "CS").current_focus == "Lit Review"

    chapter = service.create_chapter("Ch 1", 1)
    service.set_current_focus("Chapter focus", "actor1", chapter_id=chapter.id)
    assert service.repository.update_chapter_focus(chapter.id, "Chapter focus").current_focus == "Chapter focus"

def test_source_and_evidence_creation(service: DissertationService) -> None:
    chapter = service.create_chapter("Ch 1", 1)
    source = service.create_source("Source A", "book", "Cit", "actor1", locator="http://a", chapter_id=chapter.id)
    assert source.title == "Source A"
    assert source.locator == "http://a"

    evidence = service.create_evidence(source.id, "Summary", "actor1", chapter_id=chapter.id)
    assert evidence.summary == "Summary"

def test_note_creation_and_gap_resolution(service: DissertationService) -> None:
    chapter = service.create_chapter("Ch 1", 1)
    note = service.create_note("analysis", "My Note", "actor1", chapter_id=chapter.id)
    assert note.content == "My Note"

    gap = service.create_gap("Gap A", "missing_evidence", "actor1", chapter_id=chapter.id)
    resolved = service.resolve_gap(gap.id, "Resolved now", "actor1")
    assert resolved.status == "resolved"
    assert resolved.resolution_note == "Resolved now"

def test_next_action_cascade(service: DissertationService) -> None:
    # No action
    assert service.get_next_action() == "No chapters defined yet. Create Chapter I to begin."

    # Gap exists
    gap = service.create_gap("Gap A", "missing_evidence", "actor1")
    assert service.get_next_action() == "Address open gap: Gap A"

    service.repository.update_gap_status(gap.id, "in_progress")
    service.repository.update_gap_next_action(gap.id, "Do this")
    assert service.get_next_action() == "No chapters defined yet. Create Chapter I to begin."

def test_control_tower_integration_fails_without_service(service: DissertationService) -> None:
    from app.dissertation.service import DissertationConfigurationError
    import pytest
    with pytest.raises(DissertationConfigurationError):
        service.create_research_task("Task 1", "actor1")

    with pytest.raises(DissertationConfigurationError):
        service.create_decision_link("Sum", "Rat", "Imp", "App", "actor1")


class DummyWorkItem:
    def __init__(self, item_id, title, status):
        self.item_id = item_id
        self.title = title
        self.status = status

class DummyDecision:
    def __init__(self, decision_id):
        self.decision_id = decision_id

class DummyControlTowerRepository:
    def get_work_item(self, item_id):
        return DummyWorkItem(item_id, "Fetched task", "inbox")

    def get_decision(self, decision_id):
        return DummyDecision(decision_id)

class DummyControlTower:
    def __init__(self):
        self.repository = DummyControlTowerRepository()

    def capture_work(self, category, title, actor, *, summary=None, urgency=0, importance=0):
        return DummyWorkItem("wi_1", title, "open")

    def register_decision(self, summary, rationale, impact, approved_by, actor):
        return DummyDecision("dec_1")

def test_control_tower_integration(service: DissertationService) -> None:
    service.control_tower = DummyControlTower()

    chapter = service.create_chapter("Ch 1", 1)

    task_link = service.create_research_task("My task", "actor", chapter_id=chapter.id)
    assert task_link.work_item_id == "wi_1"

    tasks = service.list_research_tasks(chapter_id=chapter.id)
    assert len(tasks) == 1
    assert tasks[0]["work_item"].title == "Fetched task"

    dec_link = service.create_decision_link("s", "r", "i", "a", "actor", chapter_id=chapter.id)
    assert dec_link.decision_id == "dec_1"

    decs = service.list_decisions(chapter_id=chapter.id)
    assert len(decs) == 1
    assert decs[0]["decision"].decision_id == "dec_1"

def test_overview_and_chapter_detail(service: DissertationService) -> None:
    service.control_tower = DummyControlTower()
    service.initialize_workspace("W", "P", "actor")
    chapter = service.create_chapter("Ch 1", 1)
    source = service.create_source("S1", "book", "C1", "actor", chapter_id=chapter.id)
    evidence = service.create_evidence(source.id, "E1", "actor", chapter_id=chapter.id)
    gap = service.create_gap("G1", "missing_evidence", "actor", chapter_id=chapter.id)
    service.create_research_task("T1", "actor", chapter_id=chapter.id)

    overview = service.get_overview()
    assert overview.workspace.title == "W"
    assert len(overview.chapters) == 1
    assert overview.total_sources == 1
    assert overview.total_evidence == 1
    assert overview.open_gaps == 1
    assert overview.pending_tasks == 1
    assert overview.chapter_progress == {chapter.id: 0.0}  # per-chapter progress, draft status

    detail = service.get_chapter_detail(chapter.id)
    assert detail.chapter.title == "Ch 1"
    assert len(detail.sources) == 1
    assert len(detail.evidence) == 1
    assert detail.open_gaps == 1
    assert detail.pending_tasks == 1


def test_progress_is_computed_from_chapter_and_subchapter_statuses(service: DissertationService) -> None:
    service.initialize_workspace("Thesis", "PhD", "owner")
    chapter = service.create_chapter("Ch 1", 1)
    service.update_chapter_status(chapter.id, "revised")
    assert service.get_chapter_detail(chapter.id).progress == 75.0
    subchapter = service.create_subchapter(chapter.id, "Ch 1.1", 1)
    service.update_subchapter_status(subchapter.id, "in_review")
    assert service.get_chapter_detail(chapter.id).progress == 50.0
    assert service.get_overview().overall_progress == 50.0
