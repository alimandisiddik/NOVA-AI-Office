"""Sprint 5G deterministic multi-provider policy tests."""

from __future__ import annotations

import pytest

from app.memory.database import MemoryDatabase
from app.providers.errors import (
    AuthenticationError,
    ConnectionError,
    ProviderCancelledError,
    RateLimitError,
    UnsupportedOperationError,
)
from app.providers.models import ProviderRequest, ProviderResponse
from app.providers.repository import ProviderRepository
from app.providers.service import MAX_TOTAL_ATTEMPTS, ProviderGatewayService


class ScriptedAdapter:
    def __init__(self, results, *, available: bool = True) -> None:
        self.results = list(results)
        self.available = available
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return self.available

    async def generate_text(self, request: ProviderRequest, *, timeout_seconds: float) -> ProviderResponse:
        del timeout_seconds
        self.calls.append(request.model_id)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return ProviderResponse(request.request_id, result, f"resolved-{request.model_id}")


@pytest.fixture
def repository(tmp_path) -> ProviderRepository:
    database = MemoryDatabase(tmp_path / "provider-policy.db")
    database.initialize()
    repo = ProviderRepository(database)
    repo.initialize()
    return repo


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _service(repository, adapters) -> ProviderGatewayService:
    return ProviderGatewayService(
        repository,
        adapters,
        "https://api.example.test",
        "test-key",
        ["nova-v1"],
        ["nova-v1", "codex-direct", "claude-direct", "nova-v1-coding", "nova-v1-coding-fallback", "nova-v1-review", "nova-v1-review-fallback"],
        combo_priorities={
            "coding": ["nova-v1-coding", "nova-v1-coding-fallback"],
            "review": ["nova-v1-review", "nova-v1-review-fallback"],
        },
        # Sprint 5G.1: nova-v1-coding-fallback/nova-v1-review-fallback have
        # no evidenced distinct upstream combo and are unmapped by default
        # (see registry.py), so tests exercising attempt-accounting
        # mechanics across the full combo width give them an explicit,
        # illustrative test-only mapping here rather than relying on a
        # fabricated production default.
        upstream_route_overrides={
            "nova-v1-coding-fallback": "development-secondary-test",
            "nova-v1-review-fallback": "review-secondary-test",
        },
    )


@pytest.mark.anyio
async def test_coding_falls_back_on_provider_unavailable(repository) -> None:
    codex = ScriptedAdapter([ConnectionError("temporarily unreachable")])
    router = ScriptedAdapter(["fallback"])
    service = _service(repository, {"Codex": codex, "9Router": router})

    assert await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER") == "fallback"
    assert codex.calls == ["codex-direct"]
    assert router.calls == ["nova-v1-coding"]


@pytest.mark.anyio
async def test_coding_primary_specialist_success_skips_fallback(repository) -> None:
    """A: Codex succeeds -> 9Router combo is never attempted."""
    codex = ScriptedAdapter(["codex result"])
    router = ScriptedAdapter(["must not run"])
    service = _service(repository, {"Codex": codex, "9Router": router})

    result = await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER")

    assert result == "codex result"
    assert codex.calls == ["codex-direct"]
    assert router.calls == []


@pytest.mark.anyio
async def test_architecture_primary_specialist_success_skips_fallback(repository) -> None:
    """C: Claude succeeds -> 9Router Review Combo is never attempted."""
    claude = ScriptedAdapter(["claude result"])
    router = ScriptedAdapter(["must not run"])
    service = _service(repository, {"Claude": claude, "9Router": router})

    result = await service.generate_text("review architecture", 1, workflow_id="TECHNICAL", role_id="TECHNICAL_ARCHITECT")

    assert result == "claude result"
    assert claude.calls == ["claude-direct"]
    assert router.calls == []


@pytest.mark.anyio
async def test_architecture_falls_back_when_specialist_unavailable(repository) -> None:
    """D: Claude unavailable -> 9Router Review Combo succeeds."""
    claude = ScriptedAdapter([], available=False)
    router = ScriptedAdapter(["review fallback"])
    service = _service(repository, {"Claude": claude, "9Router": router})

    result = await service.generate_text("review architecture", 1, workflow_id="TECHNICAL", role_id="TECHNICAL_ARCHITECT")

    assert result == "review fallback"
    assert claude.calls == []
    assert router.calls == ["nova-v1-review"]


@pytest.mark.anyio
async def test_rate_limited_is_fallback_eligible(repository) -> None:
    """F: rate_limited on the primary -> eligible fallback proceeds."""
    codex = ScriptedAdapter([RateLimitError("429")])
    router = ScriptedAdapter(["fallback"])
    service = _service(repository, {"Codex": codex, "9Router": router})

    result = await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER")

    assert result == "fallback"
    with repository._db.connection() as connection:
        attempts = connection.execute(
            "SELECT * FROM provider_request_attempts ORDER BY attempt_number"
        ).fetchall()
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["error_category"] == "rate_limited"


@pytest.mark.anyio
async def test_cancelled_stops_the_chain_without_fallback(repository) -> None:
    """I: cancelled must stop immediately, never hop to another provider."""
    codex = ScriptedAdapter([ProviderCancelledError("cancelled by caller")])
    router = ScriptedAdapter(["must not run"])
    service = _service(repository, {"Codex": codex, "9Router": router})

    with pytest.raises(ProviderCancelledError):
        await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER")

    assert codex.calls == ["codex-direct"]
    assert router.calls == []


@pytest.mark.anyio
async def test_max_three_live_attempts_never_reaches_a_fourth_candidate(repository) -> None:
    """J: even with four eligible candidates, at most MAX_TOTAL_ATTEMPTS (3)
    live provider calls are ever made -- the fourth is never attempted."""
    codex = ScriptedAdapter([ConnectionError("down")])
    router = ScriptedAdapter([ConnectionError("down"), ConnectionError("down"), "must not run"])
    service = ProviderGatewayService(
        repository,
        {"Codex": codex, "9Router": router},
        "https://api.example.test",
        "test-key",
        ["nova-v1"],
        ["nova-v1", "codex-direct", "nova-v1-coding", "nova-v1-coding-fallback"],
        combo_priorities={"coding": ["nova-v1-coding", "nova-v1-coding-fallback", "nova-v1"]},
        # nova-v1-coding-fallback has no evidenced upstream mapping by
        # default (see registry.py) -- give it one here so this test's
        # fourth candidate (nova-v1) is real, proving the bound is on live
        # *attempts*, not incidentally on how many aliases happen to be
        # mapped.
        upstream_route_overrides={"nova-v1-coding-fallback": "development-secondary-test"},
    )

    with pytest.raises(ConnectionError):
        await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER")

    assert codex.calls == ["codex-direct"]
    assert router.calls == ["nova-v1-coding", "nova-v1-coding-fallback"]
    assert len(codex.calls) + len(router.calls) == MAX_TOTAL_ATTEMPTS


@pytest.mark.anyio
async def test_open_circuit_is_skipped_without_live_attempt(repository) -> None:
    """K: once a route's circuit trips open, further requests skip it
    (audited as 'skipped', not 'failed') and never consume a live attempt."""
    codex_results = [ConnectionError("down")] * 5
    codex = ScriptedAdapter(codex_results)
    router = ScriptedAdapter(["fallback"] * 5)
    service = _service(repository, {"Codex": codex, "9Router": router})

    for _ in range(5):
        assert await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER") == "fallback"

    assert service.circuit_breaker.get_state("Codex:codex-direct") == "open"

    # A 6th request must skip the now-open Codex circuit rather than call it.
    router.results.append("fallback")
    result = await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER")

    assert result == "fallback"
    assert len(codex.calls) == 5  # Codex was never called live a 6th time.
    with repository._db.connection() as connection:
        latest_request_id = connection.execute(
            "SELECT request_id FROM provider_request_audit ORDER BY rowid DESC LIMIT 1"
        ).fetchone()["request_id"]
        attempts = connection.execute(
            "SELECT * FROM provider_request_attempts WHERE request_id = ? ORDER BY attempt_number",
            (latest_request_id,),
        ).fetchall()
    assert attempts[0]["status"] == "skipped"
    assert attempts[0]["provider_id"] == "Codex"


@pytest.mark.anyio
async def test_all_candidates_failing_raises_deterministic_final_error(repository) -> None:
    """N: when every candidate in the chain fails, the request fails safely
    with the last observed error and a fully audited attempt trail."""
    codex = ScriptedAdapter([ConnectionError("down")])
    router = ScriptedAdapter([ConnectionError("down"), ConnectionError("down")])
    service = _service(repository, {"Codex": codex, "9Router": router})

    with pytest.raises(ConnectionError):
        await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER")

    with repository._db.connection() as connection:
        audit = connection.execute("SELECT * FROM provider_request_audit ORDER BY rowid DESC LIMIT 1").fetchone()
        attempts = connection.execute(
            "SELECT * FROM provider_request_attempts WHERE request_id = ? ORDER BY attempt_number",
            (audit["request_id"],),
        ).fetchall()
    assert audit["status"] == "failed"
    assert len(attempts) == 3
    assert all(attempt["status"] == "failed" for attempt in attempts)


@pytest.mark.anyio
@pytest.mark.parametrize("error", [AuthenticationError("no"), UnsupportedOperationError("bad")])
async def test_non_fallback_errors_stop_the_chain(repository, error) -> None:
    codex = ScriptedAdapter([error])
    router = ScriptedAdapter(["must not run"])
    service = _service(repository, {"Codex": codex, "9Router": router})

    with pytest.raises(type(error)):
        await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER")
    assert router.calls == []


@pytest.mark.anyio
async def test_unavailable_specialist_is_audited_without_live_attempt(repository) -> None:
    codex = ScriptedAdapter([], available=False)
    router = ScriptedAdapter(["fallback"])
    service = _service(repository, {"Codex": codex, "9Router": router})

    assert await service.generate_text("implement module", 1, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER") == "fallback"
    with repository._db.connection() as connection:
        audit = connection.execute("SELECT * FROM provider_request_audit").fetchone()
        attempts = connection.execute("SELECT * FROM provider_request_attempts ORDER BY attempt_number").fetchall()
    assert audit["attempt_count"] == 1
    assert attempts[0]["status"] == "skipped"
    assert attempts[0]["provider_id"] == "Codex"
    assert audit["final_provider_id"] == "9Router"
