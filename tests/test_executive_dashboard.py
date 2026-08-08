from __future__ import annotations

import json
import subprocess
import sys
from http import HTTPStatus
from io import BytesIO
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.agent_assignment.service import AgentAssignmentService
from app.brief.service import ExecutiveBriefService
from app.config import Settings
from app.control_tower.models import WorkItem
from app.control_tower.service import ControlTowerService
from app.dashboard import service as dashboard_service_module
from app.dashboard.server import HOST, build_dashboard_service, create_server, main, make_handler
from app.dashboard.service import DashboardService, DashboardSnapshot
from app.dashboard.views import render_dashboard
from app.dispatch.approvals import ApprovalService
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.knowledge.service import KnowledgeService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.nightshift.service import NightShiftService

FIXED_NOW = datetime(2026, 8, 8, 7, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[1]


def wire_dashboard(tmp_path: Path) -> tuple[DashboardService, ControlTowerService, WorkspaceMemoryService]:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    database.initialize()
    memory = WorkspaceMemoryService(database)
    memory.initialize()
    approvals = ApprovalService(database, authorized_user_id=7)
    approvals.initialize()
    dispatch = DispatchService(database, registry=AgentRegistry(), approvals=approvals)
    dispatch.initialize()
    assignments = AgentAssignmentService(database, registry=AgentRegistry(), dispatch=dispatch, approvals=approvals)
    assignments.initialize()
    control_tower = ControlTowerService(database, agent_assignments=assignments)
    control_tower.initialize()
    knowledge = KnowledgeService(database, memory=memory, control_tower=control_tower)
    knowledge.initialize()
    night_shift = NightShiftService(database)
    night_shift.initialize()
    brief = ExecutiveBriefService(control_tower, night_shift=night_shift, knowledge=knowledge)
    return (
        DashboardService(
            control_tower,
            memory=memory,
            agent_assignments=assignments,
            knowledge=knowledge,
            night_shift=night_shift,
            executive_brief=brief,
        ),
        control_tower,
        memory,
    )


def table_counts(database: MemoryDatabase) -> dict[str, int]:
    with database.connection() as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {name: connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}


def test_snapshot_is_bounded_and_does_not_write_canonical_tables(tmp_path: Path) -> None:
    """GET / never mutates any domain table. The sole exception is one
    control_tower_audit_log append per call, from the real, canonical
    ControlTowerService.list_approvals() aggregation the Executive Brief
    composes against (operation="approval_aggregation") -- the same audit
    trail write that already happens on every /morning or /execbrief call.
    See docs/executive-dashboard.md and app/dashboard/service.py's
    _generate_brief() for why a local non-mutating duplicate of that
    5-source aggregation was rejected in favor of reusing the canonical one.
    """
    service, control_tower, memory = wire_dashboard(tmp_path)
    project = memory.create_project("Executive Office")
    item = control_tower.capture_work("document", "Prepare review", "user:7", project_id=project.id, urgency=4, importance=4)
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    before = table_counts(database)

    snapshot = service.snapshot(FIXED_NOW)

    assert [work_item.item_id for work_item in snapshot.active_work_items] == [item.item_id]
    assert snapshot.morning_brief is not None
    assert snapshot.night_shift_mode is not None
    assert len(snapshot.active_work_items) <= 12
    after = table_counts(database)
    assert after.pop("control_tower_audit_log") == before.pop("control_tower_audit_log") + 1
    assert after == before


def test_pending_approvals_include_awaiting_approval_work_items(tmp_path: Path) -> None:
    """The dashboard's approvals view must reflect the full canonical
    aggregation, not just app/control_tower's raw approval-link table --
    regression test for the original façade dropping this source entirely.
    """
    service, control_tower, memory = wire_dashboard(tmp_path)
    project = memory.create_project("Executive Office")
    item = control_tower.capture_work("policy", "Approve vendor contract", "user:7", project_id=project.id, urgency=5, importance=5)
    control_tower.transition_work_item(item.item_id, "planned", "user:7", "Ready to plan")
    control_tower.transition_work_item(item.item_id, "in_progress", "user:7", "Work started")
    control_tower.transition_work_item(item.item_id, "awaiting_approval", "user:7", "Needs sign-off")

    snapshot = service.snapshot(FIXED_NOW)

    assert any(approval.source_item_id == item.item_id for approval in snapshot.pending_approvals)


def test_dashboard_html_escapes_user_influenced_values() -> None:
    unsafe_item = WorkItem(
        item_id="work-1", project_id=None, category="document", title="<script>alert(1)</script>", summary=None,
        priority_score=1, urgency=1, importance=1, deadline=None, dependencies=[], clarification_needs=None,
        recommended_route=None, status="in_progress", created_at="2026-08-08T00:00:00Z", updated_at="2026-08-08T00:00:00Z",
    )
    snapshot = DashboardSnapshot(
        generated_at="2026-08-08T00:00:00Z", projects=[], active_work_items=[unsafe_item], pending_approvals=[],
        pending_decisions=[], agent_assignments=[], knowledge=[], night_shift_mode=None, provider_health="Not configured",
        morning_brief=None,
    )

    html = render_dashboard(snapshot)

    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_server_is_loopback_only_and_exposes_safe_read_only_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _, _ = wire_dashboard(tmp_path)
    listener = Mock()
    monkeypatch.setattr("app.dashboard.server.ThreadingHTTPServer", listener)
    server = create_server(service, 0)
    assert server is listener.return_value
    assert listener.call_args.args[0] == (HOST, 0)

    handler = object.__new__(make_handler(service))
    handler.path = "/health"
    handler.wfile = BytesIO()
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler.do_GET()

    assert handler.send_response.call_args.args[0] == 200
    assert json.loads(handler.wfile.getvalue()) == {"status": "ok", "service": "executive-dashboard"}


def test_main_refuses_start_when_dashboard_is_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings("token", 7, "test", tmp_path / "nova.sqlite3", nova_dashboard_enabled=False)
    monkeypatch.setattr("app.dashboard.server.load_settings", lambda: settings)

    assert main() == 1


def test_build_dashboard_service_constructs_against_a_real_database(tmp_path: Path) -> None:
    """Regression test: the enabled path of `python -m app.dashboard.server`
    was never exercised by any test (only the disabled early-return and a
    hand-wired test fixture were). This previously hid a crash --
    AgentAssignmentService(database) in build_dashboard_service() called the
    real constructor without its required registry/dispatch/approvals
    keyword arguments, so `python -m app.dashboard.server` raised TypeError
    on startup with NOVA_DASHBOARD_ENABLED=true, found only by an actual
    subprocess smoke test against a real SQLite file during review."""
    db_path = tmp_path / "nova.sqlite3"
    # Provision every schema the real bot process (app/main.py) already
    # applies before the dashboard is ever started against the same file.
    wire_dashboard(tmp_path)
    settings = Settings("token", 7, "test", db_path, nova_dashboard_enabled=True, nova_dashboard_port=0)

    service = build_dashboard_service(settings)
    snapshot = service.snapshot(FIXED_NOW)

    assert snapshot.generated_at
    assert snapshot.errors == ()


def test_bot_entry_point_does_not_reference_dashboard() -> None:
    main_source = (REPO_ROOT / "app" / "main.py").read_text()

    assert "app.dashboard" not in main_source


def test_importing_dashboard_server_has_no_side_effect() -> None:
    """Importing the module must never bind a port, print, or start a server --
    only `python -m app.dashboard.server` (the __main__ guard) may do that."""
    result = subprocess.run(
        [sys.executable, "-c", "import app.dashboard.server"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert "Executive Dashboard listening" not in result.stdout


def test_write_methods_are_rejected(tmp_path: Path) -> None:
    service, _, _ = wire_dashboard(tmp_path)
    handler = object.__new__(make_handler(service))
    handler.send_error = Mock()

    handler.do_POST()
    handler.do_PUT()
    handler.do_PATCH()
    handler.do_DELETE()

    assert handler.send_error.call_count == 4
    for call in handler.send_error.call_args_list:
        assert call.args[0] == HTTPStatus.METHOD_NOT_ALLOWED


def test_unknown_get_path_returns_404(tmp_path: Path) -> None:
    service, _, _ = wire_dashboard(tmp_path)
    handler = object.__new__(make_handler(service))
    handler.path = "/../etc/passwd"
    handler.send_error = Mock()

    handler.do_GET()

    handler.send_error.assert_called_once_with(HTTPStatus.NOT_FOUND)


def test_terminal_assignments_are_excluded_from_active_view(tmp_path: Path) -> None:
    service, control_tower, memory = wire_dashboard(tmp_path)
    project = memory.create_project("Executive Office")
    active_item = control_tower.capture_work("development", "Ship feature", "user:7", project_id=project.id, urgency=3, importance=3)
    cancelled_item = control_tower.capture_work("development", "Abandoned feature", "user:7", project_id=project.id, urgency=1, importance=1)
    active_assignment = service.agent_assignments.propose_assignment(active_item.item_id, "read_only", "development_agent", "user:7")
    cancelled_assignment = service.agent_assignments.propose_assignment(cancelled_item.item_id, "read_only", "development_agent", "user:7")
    service.agent_assignments.cancel_assignment(cancelled_assignment.assignment_id, "user:7", "No longer needed")

    snapshot = service.snapshot(FIXED_NOW)

    ids = {item.assignment_id for item in snapshot.agent_assignments}
    assert active_assignment.assignment_id in ids
    assert cancelled_assignment.assignment_id not in ids


def test_snapshot_bounds_projects_and_knowledge_to_documented_maxima() -> None:
    memory = Mock()
    memory.list_projects.return_value = list(range(50))
    knowledge = Mock()
    knowledge.query.return_value = list(range(50))
    control_tower = Mock()
    control_tower.get_today_priorities.return_value = []
    night_shift = Mock()
    night_shift.get_runtime_mode.return_value = None
    agent_assignments = Mock()
    agent_assignments.list_assignments.return_value = []

    service = DashboardService(
        control_tower, memory=memory, agent_assignments=agent_assignments,
        knowledge=knowledge, night_shift=night_shift, executive_brief=None,
    )
    snapshot = service.snapshot(FIXED_NOW)

    assert len(snapshot.projects) == dashboard_service_module.MAX_PROJECTS
    assert len(snapshot.knowledge) == dashboard_service_module.MAX_KNOWLEDGE


def test_snapshot_renders_with_all_optional_dependencies_absent() -> None:
    """Provider independence / graceful-degradation: no optional collaborator
    (memory, agent_assignments, knowledge, night_shift, executive_brief) is
    required for GET / to render successfully."""
    control_tower = Mock()
    control_tower.get_today_priorities.return_value = []
    service = DashboardService(control_tower)

    snapshot = service.snapshot(FIXED_NOW)
    html = render_dashboard(snapshot)

    assert snapshot.morning_brief is None
    assert snapshot.agent_assignments == []
    assert snapshot.knowledge == []
    assert "No " in html


def test_dashboard_has_no_third_party_network_dependency() -> None:
    """Sprint 7E must add no new pip dependency and make no network call --
    only stdlib http.server/html/string plus existing NOVA services."""
    for path in (REPO_ROOT / "app" / "dashboard").glob("*.py"):
        source = path.read_text()
        for banned in ("requests", "httpx", "urllib.request", "aiohttp", "socket."):
            assert banned not in source, f"{path.name} references {banned!r}"
