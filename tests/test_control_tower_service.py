import pytest
from datetime import datetime, timezone, timedelta
from app.memory.database import MemoryDatabase
from app.control_tower.service import ControlTowerService
from app.control_tower.models import WORK_ITEM_STATES
from app.control_tower.repository import InvalidStateTransitionError, InvalidCategoryError, ControlTowerError

@pytest.fixture
def database(tmp_path):
    db = MemoryDatabase(tmp_path / "test.db")
    db.initialize()
    return db

@pytest.fixture
def service(database):
    svc = ControlTowerService(database)
    svc.initialize()
    return svc

def test_capture_work(service):
    item = service.capture_work(
        category="document",
        title="Draft sprint spec",
        actor="user",
        urgency=5,
        importance=5
    )
    assert item.category == "document"
    assert item.title == "Draft sprint spec"
    assert item.status == "inbox"
    assert item.priority_score == 25 # (5*2) + (5*3)

def test_capture_invalid_category(service):
    with pytest.raises(InvalidCategoryError):
        service.capture_work(category="unknown_cat", title="test", actor="user")

def test_capture_sensitive_content(service):
    with pytest.raises(ControlTowerError, match="Sensitive content"):
        # Use a string that definitively matches SENSITIVE_CONTENT_PATTERN in security.py
        service.capture_work(category="document", title="API_KEY=xyz123", actor="user")

def test_transition_work_item(service):
    item = service.capture_work("document", "test", "user")
    service.transition_work_item(item.item_id, "in_progress", "user", "start work")
    updated = service.repository.get_work_item(item.item_id)
    assert updated.status == "in_progress"

def test_invalid_transition(service):
    item = service.capture_work("document", "test", "user")
    with pytest.raises(InvalidStateTransitionError):
        # Cannot transition from inbox straight to completed
        service.transition_work_item(item.item_id, "completed", "user", "done")

def test_today_priorities_ordering(service):
    service.capture_work("document", "low priority", "user", urgency=1, importance=1)
    service.capture_work("document", "high priority", "user", urgency=10, importance=10)

    priorities = service.get_today_priorities()
    assert len(priorities) == 2
    assert priorities[0].title == "high priority"
    assert priorities[1].title == "low priority"

def test_decision_register(service):
    decision = service.register_decision(
        summary="Use SQLite for metadata",
        rationale="Simplicity",
        impact="Low ops burden",
        approved_by="Architect",
        actor="user"
    )
    assert decision.status == "active"
    assert decision.summary == "Use SQLite for metadata"

def test_approval_flow(service):
    approval = service.request_approval("execution", "script-123", "Run script.py", "user")
    assert approval.status == "pending"

    approvals = service.list_approvals()
    assert len(approvals) == 1

    service.resolve_approval(approval.approval_id, True, "user")

    resolved = service.repository.get_approval(approval.approval_id)
    assert resolved.status == "approved"
    assert resolved.resolved_by == "user"

def test_evening_shutdown(service):
    item1 = service.capture_work("document", "test 1", "user")
    service.transition_work_item(item1.item_id, "in_progress", "user", "start")

    item2 = service.capture_work("document", "test 2", "user")
    service.transition_work_item(item2.item_id, "planned", "user", "plan")

    summary = service.evening_shutdown()
    assert len(summary.in_progress) == 1
    assert len(summary.moved_to_tomorrow) == 1
    assert not summary.night_shift_required

def test_morning_brief(service):
    service.capture_work("document", "today task", "user", urgency=10, importance=10)
    service.request_approval("test", "test", "action", "user")

    brief = service.morning_brief()
    assert len(brief.today_priorities) == 1
    assert len(brief.approval_queue) == 1

def test_capture_dependency_validation_and_resolved_blockers(service):
    with pytest.raises(ControlTowerError):
        service.capture_work("document", "Missing dependency", "user", dependencies=["missing"])

    completed = service.capture_work("document", "Complete prerequisite", "user")
    service.transition_work_item(completed.item_id, "in_progress", "user", "start")
    service.transition_work_item(completed.item_id, "completed", "user", "finish")
    dependent = service.capture_work("document", "Dependent", "user", dependencies=[completed.item_id])
    assert service.unresolved_blocker_count(dependent) == 0

    blocking = service.capture_work("document", "Blocking prerequisite", "user")
    dependent = service.capture_work("document", "Blocked", "user", dependencies=[blocking.item_id])
    assert service.unresolved_blocker_count(dependent) == 1
    with pytest.raises(ControlTowerError):
        service.capture_work("document", "Duplicate", "user", dependencies=[blocking.item_id, blocking.item_id])


def test_capture_validation_and_route_storage(service):
    with pytest.raises(ControlTowerError):
        service.capture_work("document", "   ", "user")
    with pytest.raises(ControlTowerError):
        service.capture_work("document", "x" * 201, "user")
    with pytest.raises(ControlTowerError):
        service.capture_work("document", "Bad deadline", "user", deadline="tomorrow")
    item = service.capture_work("presentation", "Deck", "user", deadline="2026-08-07T09:00")
    assert item.recommended_route == "Presentation Agent"
    assert item.deadline.endswith("+00:00")

def test_schema_migration_is_idempotent_for_earlier_audit_table(database):
    from app.control_tower.schema import apply_schema

    with database.connection() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS control_tower_audit_log ("
            "log_id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, "
            "action TEXT NOT NULL, from_state TEXT, to_state TEXT, actor TEXT NOT NULL, "
            "reason TEXT, timestamp TEXT NOT NULL)"
        )
        apply_schema(connection)
        apply_schema(connection)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(control_tower_audit_log)")}
    assert {"operation", "correlation_id", "outcome", "metadata"} <= columns
