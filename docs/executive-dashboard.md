# Executive Dashboard

## Purpose

The Executive Dashboard is NOVA's minimal, read-only situational-awareness
view. It composes canonical local state and is not a workflow, provider, or
persistence layer.

## Startup and access

The dashboard is disabled by default. Set `NOVA_DASHBOARD_ENABLED=true`, then
start it explicitly with:

```bash
python -m app.dashboard.server
```

It binds only to `127.0.0.1` on `NOVA_DASHBOARD_PORT` (default `8787`). The
health endpoint is `GET /health`; it returns only a generic service status.
`app/main.py` never imports or starts the dashboard.

## Architecture and data

`DashboardService` is the sole dashboard composition boundary. It reads bounded
results from `WorkspaceMemoryService`, `ControlTowerService`,
`AgentAssignmentService`, `KnowledgeService`, `NightShiftService`, and
`ExecutiveBriefService`. Provider information is configuration-state only and
is never probed, so startup does not require a provider or network access.

The first screen displays the Executive Brief, projects, active work,
assignments, provenance-visible knowledge, Night Shift/provider status, and
approval/decision summary. Output is bounded and all rendered text is HTML
escaped.

## Security and intentional limits

There are no write routes, forms, action controls, background workers,
WebSockets, dashboard tables, schemas, caches, or external dependencies. The
server does not render database paths, credentials, environment values, tokens,
or provider configuration. Public hosting, authentication, TLS, multi-user
access, and all write controls are intentionally deferred.

## Known limitation: one audit-log append per page load

`DashboardService` composes the Executive Brief panel (and, through it, the
Approval / Blocker Summary) by calling `ExecutiveBriefService.generate_morning_brief()`,
which calls the real, canonical `ControlTowerService.list_approvals()` — the
same read-only aggregation method already used by `/morning` and `/execbrief`.
That method's five-source composition (approval links, work items awaiting
approval, executions, Night Shift jobs, dispatch approvals) is 7A-owned,
cross-domain logic that this sprint does not duplicate. Reusing it means every
`GET /` appends exactly one row to `control_tower_audit_log`
(`operation="approval_aggregation"`, `actor="system"`) — the only table any
dashboard request ever writes to. No `WorkItem`, `AgentAssignment`,
`KnowledgeItem`, `Approval`, or `Project` row is ever created, changed, or
deleted by the dashboard.

An earlier version of this sprint avoided that audit write with a local
`list_approvals()` shim that called only `control_tower_approval_links`
directly — but that silently dropped the other four approval sources,
understating pending approvals on the one panel this dashboard exists to get
right. A single, well-understood audit-trail append (identical to existing
`/morning`/`/execbrief` behavior) was judged the safer trade-off over a
locally duplicated, drift-prone copy of another sprint's aggregation logic.
See `tests/test_executive_dashboard.py::test_snapshot_is_bounded_and_does_not_write_canonical_tables`
and `::test_pending_approvals_include_awaiting_approval_work_items`.
