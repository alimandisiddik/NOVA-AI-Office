"""Closed adapter seam; Sprint 5B.1 intentionally performs no real work."""

from __future__ import annotations

from typing import Protocol

from app.dispatch.models import DispatchRecord, DispatchResult
from app.dispatch.repository import utc_now


class AgentAdapter(Protocol):
    def execute(self, dispatch: DispatchRecord, attempt_number: int | None = None) -> DispatchResult:
        """Return a safe, structured result without external side effects."""


class LocalDeterministicAgentAdapter:
    """Non-side-effecting adapter used until a future approved adapter exists."""

    def execute(self, dispatch: DispatchRecord, attempt_number: int | None = None) -> DispatchResult:
        return DispatchResult(
            dispatch_id=dispatch.dispatch_id,
            attempt_number=dispatch.attempt_count if attempt_number is None else attempt_number,
            success=True,
            summary="Local deterministic dispatch completed.",
            ended_at=utc_now(),
        )


_ADAPTERS: dict[str, type[AgentAdapter]] = {"local_deterministic": LocalDeterministicAgentAdapter}


def get_adapter(adapter_id: str) -> AgentAdapter:
    try:
        return _ADAPTERS[adapter_id]()
    except KeyError as error:
        from app.dispatch.errors import DispatchUnavailableError
        raise DispatchUnavailableError("Dispatch adapter is unavailable.") from error
