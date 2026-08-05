"""Provider-agnostic model router for NOVA AI Office.

Subsystem layout
----------------
roles       -- logical role registry (what each role is responsible for)
workflows   -- workflow registry (canonical workflow definitions)
classifier  -- deterministic intent → workflow classifier
risk        -- risk level classifier and approval policy  ← shared layer
planner     -- execution-plan generator (roles + workflow + risk + approval)

Sprint dependency
-----------------
Sprint 3 (Execution Orchestration, ``app/execution/``) depends on the
**reviewed** Sprint 3A risk engine implemented in ``app/router/risk.py``.
``app/router`` is the single shared routing and risk-policy layer.

Do NOT create a duplicate policy classifier (e.g. ``app/execution/policy.py``).
All risk assessment must flow through ``app.router.risk.assess_risk``.

No real AI provider is called anywhere in this package.
"""

from app.router.planner import ExecutionPlan, generate_plan
from app.router.roles import Role, get_role, list_roles
from app.router.workflows import Workflow, get_workflow, list_workflows

__all__ = [
    "ExecutionPlan",
    "Role",
    "Workflow",
    "generate_plan",
    "get_role",
    "get_workflow",
    "list_roles",
    "list_workflows",
]
