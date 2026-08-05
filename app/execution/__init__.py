"""Execution orchestration package for NOVA AI Office — Sprint 3.

Subsystem layout
----------------
models      -- immutable domain dataclasses (ExecutionRecord, AuditEntry)
schema      -- additive SQLite schema (executions + execution_audit_log)
repository  -- all SQL: parameterized queries only, no business logic
service     -- ExecutionService: state-machine, approval, sensitive-input guard
adapter     -- LocalDeterministicAdapter: in-process, no shell, no provider
formatters  -- Telegram message rendering (no SQL, no business logic)

No real AI provider, shell, subprocess, or network call is made anywhere
in this package.
"""

from app.execution.models import ExecutionRecord, ExecutionState
from app.execution.service import ExecutionService

__all__ = ["ExecutionRecord", "ExecutionService", "ExecutionState"]
