from pathlib import Path
from app.dissertation.service import DissertationService
from app.memory.database import MemoryDatabase
import pytest

class DummyWorkItem:
    def __init__(self, item_id, title, status):
        self.item_id = item_id
        self.title = title
        self.status = status

class DummyControlTowerRepository:
    def __init__(self):
        self.items = {}

    def get_work_item(self, item_id):
        return self.items.get(item_id)

class DummyControlTower:
    def __init__(self):
        self.repository = DummyControlTowerRepository()

    def capture_work(self, category, title, actor, *, summary=None, urgency=0, importance=0):
        item = DummyWorkItem(f"wi_{len(self.repository.items)}", title, "inbox")
        self.repository.items[item.item_id] = item
        return item

@pytest.fixture
def service(tmp_path: Path) -> DissertationService:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    database.initialize()
    srv = DissertationService(database, control_tower=DummyControlTower())
    srv.initialize()
    srv.initialize_workspace("Thesis", "PhD", "owner")
    return srv

def test_next_action_no_chapters(service: DissertationService) -> None:
    assert service.get_next_action() == "No chapters defined yet. Create Chapter I to begin."

def test_next_action_all_chapters_final(service: DissertationService) -> None:
    ch = service.create_chapter("Intro", 1)
    service.update_chapter_status(ch.id, "final")
    assert service.get_next_action() == "All chapters are final. Consider a full-dissertation review pass."

def test_next_action_chapter_without_tasks(service: DissertationService) -> None:
    ch = service.create_chapter("Intro", 1)
    assert service.get_next_action() == "Define next research task for Chapter 1 (Intro)"

def test_next_action_pending_task(service: DissertationService) -> None:
    ch = service.create_chapter("Intro", 1)
    task_link = service.create_research_task("My task", "owner", chapter_id=ch.id)

    assert service.get_next_action() == "Continue task: My task (Chapter 1)"

def test_next_action_open_gap(service: DissertationService) -> None:
    ch = service.create_chapter("Intro", 1)
    gap = service.create_gap("Missing info", "missing_evidence", "owner", chapter_id=ch.id)
    assert service.get_next_action() == "Address open gap: Missing info (Chapter 1)"

def test_next_action_critical_gap_beats_pending_task(service: DissertationService) -> None:
    ch = service.create_chapter("Intro", 1)
    # Create task
    task_link = service.create_research_task("My task", "owner", chapter_id=ch.id)

    # Create critical gap
    gap = service.create_gap("Missing critical info", "missing_evidence", "owner", chapter_id=ch.id, priority="critical")

    assert service.get_next_action() == "Resolve critical gap: Missing critical info (Chapter 1)"

def test_next_action_pending_task_beats_normal_gap(service: DissertationService) -> None:
    ch = service.create_chapter("Intro", 1)
    # Create normal gap
    gap = service.create_gap("Missing info", "missing_evidence", "owner", chapter_id=ch.id, priority="normal")

    # Create task
    task_link = service.create_research_task("My task", "owner", chapter_id=ch.id)

    assert service.get_next_action() == "Continue task: My task (Chapter 1)"
