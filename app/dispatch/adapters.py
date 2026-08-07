"""Closed adapter seam; Sprint 5B.1 intentionally performs no real work."""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Protocol, Any, Callable, Coroutine

from app.dispatch.models import DispatchRecord, DispatchResult
from app.dispatch.repository import utc_now


def _run_coroutine_sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async provider call from a synchronous adapter boundary safely.

    ``DispatchService.dispatch()`` is a synchronous method invoked both from
    plain synchronous callers (tests, and the Night Shift tick) and from
    inside python-telegram-bot's already-running asyncio event loop (the
    Night Shift scheduler callback and the ``/approve``/``/retrydispatch``
    handlers all execute on that loop). ``asyncio.run()`` raises
    ``RuntimeError`` if a loop is already running on the current thread, so
    that case is bridged onto a dedicated helper thread with its own loop
    instead of assuming no loop is running.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


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


class ProviderGatewayAgentAdapter:
    """Agent adapter that calls the ProviderGatewayService (5G)."""

    _service_factory: Callable[[], Any] | None = None

    @classmethod
    def set_service_factory(cls, factory: Callable[[], Any]) -> None:
        # Stored via staticmethod() so instance access (`self._service_factory`)
        # returns the plain callable — without this, Python's function
        # descriptor protocol turns a bare function/lambda assigned as a class
        # attribute into a bound method on instance access, silently injecting
        # `self` as an unwanted first positional argument and raising
        # TypeError on every call.
        cls._service_factory = staticmethod(factory)

    def execute(self, dispatch: DispatchRecord, attempt_number: int | None = None) -> DispatchResult:
        if self._service_factory is None:
            return DispatchResult(
                dispatch_id=dispatch.dispatch_id,
                attempt_number=dispatch.attempt_count if attempt_number is None else attempt_number,
                success=False,
                summary="ProviderGatewayService not initialized.",
                ended_at=utc_now(),
            )

        service = self._service_factory()
        if service is None:
            return DispatchResult(
                dispatch_id=dispatch.dispatch_id,
                attempt_number=dispatch.attempt_count if attempt_number is None else attempt_number,
                success=False,
                summary="ProviderGatewayService is disabled in configuration.",
                ended_at=utc_now(),
            )

        try:
            route = {
                "coding_agent": ("TECHNICAL", "EXECUTION_WORKER"),
                "architecture_agent": ("TECHNICAL", "TECHNICAL_ARCHITECT"),
                "generic_ai_agent": ("GENERAL", "CONTROL_TOWER"),
            }.get(dispatch.agent_id, ("GENERAL", "CONTROL_TOWER"))
            content = _run_coroutine_sync(
                service.generate_text(
                    f"Dispatch reference: {dispatch.payload_ref}",
                    user_id=0,
                    workflow_id=route[0],
                    role_id=route[1],
                )
            )
            return DispatchResult(
                dispatch_id=dispatch.dispatch_id,
                attempt_number=dispatch.attempt_count if attempt_number is None else attempt_number,
                success=True,
                summary=f"Provider returned {len(content.encode('utf-8'))} bytes.",
                ended_at=utc_now(),
            )
        except Exception as error:
            return DispatchResult(
                dispatch_id=dispatch.dispatch_id,
                attempt_number=dispatch.attempt_count if attempt_number is None else attempt_number,
                success=False,
                summary=f"Provider execution failed: {type(error).__name__}.",
                ended_at=utc_now(),
            )


_ADAPTERS: dict[str, type[AgentAdapter]] = {
    "local_deterministic": LocalDeterministicAgentAdapter,
    "provider_gateway": ProviderGatewayAgentAdapter,
}


def get_adapter(adapter_id: str) -> AgentAdapter:
    try:
        return _ADAPTERS[adapter_id]()
    except KeyError as error:
        from app.dispatch.errors import DispatchUnavailableError
        raise DispatchUnavailableError("Dispatch adapter is unavailable.") from error
