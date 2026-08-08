"""Deterministic service coverage for provenance-first knowledge records."""

from pathlib import Path

import pytest

from app.control_tower.service import ControlTowerService
from app.knowledge.repository import KnowledgeSourceNotFoundError
from app.knowledge.service import InvalidKnowledgeValueError, KnowledgeService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService


def knowledge(tmp_path: Path, *, with_references: bool = False) -> tuple[KnowledgeService, WorkspaceMemoryService | None, ControlTowerService | None]:
    database = MemoryDatabase(tmp_path / "knowledge.sqlite3")
    database.initialize()
    memory = WorkspaceMemoryService(database) if with_references else None
    if memory is not None:
        memory.initialize()
    control_tower = ControlTowerService(database) if with_references else None
    if control_tower is not None:
        control_tower.initialize()
    service = KnowledgeService(database, memory=memory, control_tower=control_tower)
    service.initialize()
    return service, memory, control_tower


def test_create_and_query_always_return_source_provenance(tmp_path: Path) -> None:
    service, _, _ = knowledge(tmp_path)
    source = service.create_source("Strategy notes", "document", "Internal strategy notes, 2026", "owner")
    item = service.create_item(source.id, "Customer focus", "Focus on retained customers.", "owner", tags="strategy,customers")

    results = service.query(keyword="customers")

    assert len(results) == 1
    assert results[0].item == item
    assert results[0].source == source
    assert results[0].source.citation_text == "Internal strategy notes, 2026"


def test_query_filters_by_keyword_tag_project_and_empty_result(tmp_path: Path) -> None:
    service, memory, _ = knowledge(tmp_path, with_references=True)
    assert memory is not None
    project = memory.create_project("Alpha", "")
    source = service.create_source("Board notes", "note", "Board notes, August 2026", "owner")
    service.create_item(source.id, "Margin plan", "Improve service margin.", "owner", tags="finance, board", project_id=project.id)
    service.create_item(source.id, "Hiring plan", "Expand support capacity.", "owner", tags="people")

    assert [result.item.title for result in service.query(keyword="margin")] == ["Margin plan"]
    assert [result.item.title for result in service.query(tag="finance")] == ["Margin plan"]
    assert [result.item.title for result in service.query(project_id=project.id)] == ["Margin plan"]
    assert service.query(keyword="missing") == []


def test_create_item_rejects_unknown_source_without_persisting(tmp_path: Path) -> None:
    service, _, _ = knowledge(tmp_path)

    with pytest.raises(KnowledgeSourceNotFoundError, match="Knowledge source not found"):
        service.create_item(999, "Finding", "Summary", "owner")

    assert service.query() == []


def test_loose_references_require_existing_project_and_work_item(tmp_path: Path) -> None:
    service, memory, control_tower = knowledge(tmp_path, with_references=True)
    assert memory is not None
    assert control_tower is not None
    project = memory.create_project("Alpha", "")
    work_item = control_tower.capture_work("academic", "Review evidence", "owner", project_id=project.id)
    source = service.create_source("Research note", "note", "Research note, 2026", "owner")

    item = service.create_item(
        source.id, "Validated finding", "Summary", "owner", project_id=project.id, work_item_id=work_item.item_id
    )

    assert item.project_id == project.id
    assert item.work_item_id == work_item.item_id
    with pytest.raises(InvalidKnowledgeValueError, match="Project not found"):
        service.create_item(source.id, "Invalid project", "Summary", "owner", project_id=999)
    with pytest.raises(InvalidKnowledgeValueError, match="Work item not found"):
        service.create_item(source.id, "Invalid work item", "Summary", "owner", work_item_id="missing")


@pytest.mark.parametrize("source_type", ["book", "", "DRIVE"])
def test_create_source_rejects_invalid_source_type(tmp_path: Path, source_type: str) -> None:
    service, _, _ = knowledge(tmp_path)

    with pytest.raises(InvalidKnowledgeValueError, match="Invalid source type"):
        service.create_source("Title", source_type, "Citation", "owner")


@pytest.mark.parametrize("confidence", ["EXTREME", "", "certain"])
def test_create_item_rejects_invalid_confidence(tmp_path: Path, confidence: str) -> None:
    service, _, _ = knowledge(tmp_path)
    source = service.create_source("Title", "manual", "Citation", "owner")

    with pytest.raises(InvalidKnowledgeValueError, match="Invalid confidence"):
        service.create_item(source.id, "Finding", "Summary", "owner", confidence=confidence)


def test_query_result_count_is_bounded(tmp_path: Path) -> None:
    from app.knowledge.repository import MAX_QUERY_RESULTS

    service, _, _ = knowledge(tmp_path)
    source = service.create_source("Title", "manual", "Citation", "owner")
    for index in range(MAX_QUERY_RESULTS + 5):
        service.create_item(source.id, f"Finding {index}", "Summary text", "owner")

    results = service.query()

    assert len(results) == MAX_QUERY_RESULTS
