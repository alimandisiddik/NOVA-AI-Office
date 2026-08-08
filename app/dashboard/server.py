"""Standalone localhost-only entry point for the Executive Dashboard."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.agent_assignment.service import AgentAssignmentService
from app.brief.service import ExecutiveBriefService
from app.config import ConfigurationError, Settings, load_settings
from app.control_tower.service import ControlTowerService
from app.dashboard.service import DashboardService
from app.dashboard.views import render_dashboard, render_error
from app.dispatch.approvals import ApprovalService
from app.dispatch.registry import AgentRegistry
from app.dispatch.service import DispatchService
from app.knowledge.service import KnowledgeService
from app.memory.database import MemoryDatabase
from app.memory.services import WorkspaceMemoryService
from app.nightshift.service import NightShiftService

HOST = "127.0.0.1"
LOGGER = logging.getLogger(__name__)


def build_dashboard_service(settings: Settings) -> DashboardService:
    """Construct independent, read-only service instances for the shared SQLite DB."""
    database = MemoryDatabase(settings.nova_memory_db_path)
    memory = WorkspaceMemoryService(database)
    # registry/dispatch/approvals are required constructor collaborators for
    # AgentAssignmentService, but the dashboard only ever calls its read
    # method (.list_assignments()) -- no dispatch/approval mutation is
    # reachable from this process. Read-only: only .list_pending() is ever
    # called on `approvals` (via ControlTowerService's canonical
    # list_approvals() aggregation). .initialize()/approve()/reject()/
    # create_dispatch()/dispatch() are never invoked from the dashboard.
    registry = AgentRegistry()
    approvals = ApprovalService(database, authorized_user_id=settings.telegram_allowed_user_id)
    dispatch = DispatchService(database, registry=registry, approvals=approvals)
    assignments = AgentAssignmentService(database, registry=registry, dispatch=dispatch, approvals=approvals)
    control_tower = ControlTowerService(database, agent_assignments=assignments, approvals=approvals)
    knowledge = KnowledgeService(database, memory=memory, control_tower=control_tower)
    night_shift = NightShiftService(database)
    brief = ExecutiveBriefService(control_tower, night_shift=night_shift, knowledge=knowledge)
    provider_health = "Configured (not probed)" if settings.nova_provider_base_url and settings.nova_provider_api_key else "Not configured"
    return DashboardService(
        control_tower,
        memory=memory,
        agent_assignments=assignments,
        knowledge=knowledge,
        night_shift=night_shift,
        executive_brief=brief,
        provider_health=provider_health,
    )


def make_handler(service: DashboardService) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                self._send_json(HTTPStatus.OK, {"status": "ok", "service": "executive-dashboard"})
                return
            if self.path != "/":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                self._send_html(HTTPStatus.OK, render_dashboard(service.snapshot()))
            except Exception:
                LOGGER.exception("Dashboard rendering failed")
                self._send_html(HTTPStatus.SERVICE_UNAVAILABLE, render_error("Dashboard data is temporarily unavailable."))

        def do_POST(self) -> None:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def log_message(self, format: str, *args: object) -> None:
            LOGGER.info("Dashboard request: " + format, *args)

        def _send_html(self, status: HTTPStatus, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, status: HTTPStatus, body: dict[str, str]) -> None:
            encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return DashboardHandler


def create_server(service: DashboardService, port: int) -> ThreadingHTTPServer:
    """Create a loopback-only listener; host is intentionally not configurable."""
    return ThreadingHTTPServer((HOST, port), make_handler(service))


def main() -> int:
    try:
        settings = load_settings()
    except ConfigurationError as error:
        print(f"Dashboard configuration error: {error}")
        return 1
    if not settings.nova_dashboard_enabled:
        print("Executive Dashboard is disabled. Set NOVA_DASHBOARD_ENABLED=true to start it.")
        return 1
    server = create_server(build_dashboard_service(settings), settings.nova_dashboard_port)
    print(f"Executive Dashboard listening on http://{HOST}:{settings.nova_dashboard_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
