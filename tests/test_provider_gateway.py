"""Sprint 4A/4B provider gateway tests using only httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from app.memory.database import MemoryDatabase, MemoryDatabaseError
from app.providers.errors import (
    AuthenticationError,
    AuthorizationError,
    CircuitOpenError,
    ConfigurationError,
    ConnectionError,
    InvalidResponseError,
    OutputLimitError,
    ProviderError,
    SensitiveContentError,
    TimeoutError,
)
from app.providers.models import ProviderAuditRecord, ProviderRequestAttempt
from app.providers.ninerouter import NineRouterAdapter
from app.providers.repository import ProviderRepository, _utc_now, hash_text
from app.providers.service import ProviderGatewayService

AUTHORIZED_USER = 111
MODEL_1 = "nova-v1"
MODEL_2 = "nova-v1-fallback"
MODEL_3 = "nova-v1-fast"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def repo(tmp_path) -> ProviderRepository:
    database = MemoryDatabase(tmp_path / "provider.db")
    database.initialize()
    repository = ProviderRepository(database)
    repository.initialize()
    return repository


def _service(repo: ProviderRepository, handler, priority: list[str] | None = None) -> ProviderGatewayService:
    configured_priority = priority or [MODEL_1]
    adapter = NineRouterAdapter(
        "https://api.example.test",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    return ProviderGatewayService(
        repo,
        adapter,
        "https://api.example.test",
        "test-key",
        configured_priority,
        configured_priority,
    )


def _success_response(model_id: str, content: str = "ok") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "mock-request",
            "model": model_id,
            "choices": [{"message": {"role": "assistant", "content": content}}],
        },
    )


def test_configuration_rejects_empty_priority(repo) -> None:
    with pytest.raises(ConfigurationError, match="empty"):
        ProviderGatewayService(repo, None, "https://api.example.test", "key", [], [MODEL_1])


def test_configuration_rejects_duplicate_priority_ids(repo) -> None:
    with pytest.raises(ConfigurationError, match="duplicate"):
        ProviderGatewayService(
            repo,
            None,
            "https://api.example.test",
            "key",
            [MODEL_1, MODEL_1],
            [MODEL_1],
        )


def test_configuration_rejects_disallowed_model(repo) -> None:
    with pytest.raises(ConfigurationError, match="allowed"):
        ProviderGatewayService(
            repo,
            None,
            "https://api.example.test",
            "key",
            [MODEL_1],
            [MODEL_2],
        )


def test_configuration_rejects_priority_over_attempt_limit(repo) -> None:
    with pytest.raises(ConfigurationError, match="exceeds"):
        ProviderGatewayService(
            repo,
            None,
            "https://api.example.test",
            "key",
            [MODEL_1, MODEL_2, MODEL_3, "nova-v1-isolated"],
            [MODEL_1, MODEL_2, MODEL_3, "nova-v1-isolated"],
        )


@pytest.mark.anyio
async def test_successful_response_audits_one_attempt(repo) -> None:
    service = _service(repo, lambda request: _success_response(MODEL_1, "Hello"))
    service.initialize()

    assert await service.generate_text("Hello there", AUTHORIZED_USER) == "Hello"
    with repo._db.connection() as connection:
        audit = connection.execute("SELECT * FROM provider_request_audit").fetchone()
        attempts = connection.execute("SELECT * FROM provider_request_attempts").fetchall()

    assert audit["initial_model_id"] == MODEL_1
    assert audit["final_model_id"] == MODEL_1
    assert audit["attempt_count"] == 1
    assert audit["fallback_used"] == 0
    assert len(attempts) == 1


@pytest.mark.anyio
async def test_timeout_falls_back_once_without_confirmation(repo) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model_id = __import__("json").loads(request.content)["model"]
        calls.append(model_id)
        if len(calls) == 1:
            raise httpx.TimeoutException("timeout")
        return _success_response(MODEL_2, "fallback")

    service = _service(repo, handler, [MODEL_1, MODEL_2])
    assert await service.generate_text("Hello there", AUTHORIZED_USER) == "fallback"
    assert calls == [MODEL_1, MODEL_2]
    assert service.last_successful_model == MODEL_2
    assert service.last_fallback_reason == "timeout_error"


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [502, 503, 504])
async def test_only_listed_5xx_fall_back(repo, status_code: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status_code, json={"error": "temporary"})
        return _success_response(MODEL_2, "fallback")

    service = _service(repo, handler, [MODEL_1, MODEL_2])
    assert await service.generate_text("Hello there", AUTHORIZED_USER) == "fallback"
    assert calls == 2


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [500, 501, 505, 506])
async def test_unlisted_5xx_stop_without_fallback(repo, status_code: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json={"error": "permanent"})

    service = _service(repo, handler, [MODEL_1, MODEL_2])
    with pytest.raises(ProviderError):
        await service.generate_text("Hello there", AUTHORIZED_USER)
    assert calls == 1


@pytest.mark.anyio
async def test_runtime_never_attempts_a_model_twice(repo) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(__import__("json").loads(request.content)["model"])
        raise httpx.TimeoutException("timeout")

    service = _service(repo, handler, [MODEL_1, MODEL_2, MODEL_3])
    with pytest.raises(TimeoutError):
        await service.generate_text("Hello there", AUTHORIZED_USER)
    assert calls == [MODEL_1, MODEL_2, MODEL_3]
    assert len(calls) == len(set(calls))


@pytest.mark.anyio
async def test_model_circuits_are_independent(repo) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502, json={"error": "temporary"})

    service = _service(repo, handler, [MODEL_1])
    for _ in range(5):
        with pytest.raises(ConnectionError):
            await service.generate_text("Hello there", AUTHORIZED_USER)

    assert service.circuit_breaker.get_state(MODEL_1) == "open"
    assert service.circuit_breaker.get_state(MODEL_2) == "closed"
    with pytest.raises(ConfigurationError, match="No eligible"):
        await service.generate_text("Hello there", AUTHORIZED_USER)
    assert calls == 5


@pytest.mark.anyio
async def test_sensitive_and_destructive_prompts_make_no_network_calls(repo) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _success_response(MODEL_1)

    service = _service(repo, handler)
    with pytest.raises(SensitiveContentError):
        await service.generate_text("PASSWORD=never-send-this", AUTHORIZED_USER)
    with pytest.raises(AuthorizationError):
        await service.generate_text("rm -rf /", AUTHORIZED_USER)
    assert calls == 0


@pytest.mark.anyio
async def test_adapter_invalid_json_stops_without_fallback(repo) -> None:
    service = _service(repo, lambda request: httpx.Response(200, content=b"not json"), [MODEL_1, MODEL_2])
    with pytest.raises(InvalidResponseError):
        await service.generate_text("Hello there", AUTHORIZED_USER)


@pytest.mark.anyio
async def test_adapter_output_limit_is_enforced(repo) -> None:
    service = _service(repo, lambda request: _success_response(MODEL_1, "x" * 70_000))
    with pytest.raises(OutputLimitError):
        await service.generate_text("Hello there", AUTHORIZED_USER)


def test_repository_rejects_duplicate_attempt_number(repo) -> None:
    audit = ProviderAuditRecord(
        request_id="request-1",
        execution_id=None,
        user_id=AUTHORIZED_USER,
        provider_id="9Router",
        model_id=MODEL_1,
        workflow_id="GENERAL",
        role_id="CONTROL_TOWER",
        status="success",
        prompt_hash=hash_text("Hello there"),
        response_size=2,
        latency_ms=1,
        retry_count=0,
        error_category=None,
        created_at=_utc_now(),
        completed_at=_utc_now(),
    )
    duplicate_attempts = [
        ProviderRequestAttempt(1, MODEL_1, 1, None, "success", _utc_now()),
        ProviderRequestAttempt(1, MODEL_2, 1, "timeout_error", "failed", _utc_now()),
    ]
    with pytest.raises(MemoryDatabaseError, match="Duplicate attempt"):
        repo.log_request(audit, duplicate_attempts)


def test_migrates_sprint_4a_database_idempotently(tmp_path) -> None:
    database = MemoryDatabase(tmp_path / "legacy-4a.db")
    database.initialize()
    with database.connection() as connection:
        connection.execute(
            """
            CREATE TABLE provider_request_audit (
                request_id TEXT PRIMARY KEY, execution_id INTEGER, user_id INTEGER NOT NULL,
                provider_id TEXT NOT NULL, model_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
                role_id TEXT NOT NULL, status TEXT NOT NULL, prompt_hash TEXT NOT NULL,
                response_size INTEGER NOT NULL, latency_ms INTEGER NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0, error_category TEXT,
                created_at TEXT NOT NULL, completed_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO provider_request_audit VALUES (
                'legacy', NULL, 1, '9Router', 'nova-v1', 'GENERAL', 'CONTROL_TOWER',
                'success', 'hash', 10, 1, 0, NULL, 'created', 'completed'
            )
            """
        )

    repository = ProviderRepository(database)
    repository.initialize()
    repository.initialize()
    with database.connection() as connection:
        row = connection.execute("SELECT * FROM provider_request_audit WHERE request_id = 'legacy'").fetchone()
        attempt_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'provider_request_attempts'"
        ).fetchone()

    assert row["initial_model_id"] is None
    assert row["attempt_count"] == 1
    assert attempt_table is not None

@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["rate_limit", "connection"])
async def test_structured_retryable_errors_fall_back(repo, failure: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1 and failure == "rate_limit":
            return httpx.Response(429, json={"error": "rate limit"})
        if calls == 1:
            raise httpx.ConnectError("connection failed", request=request)
        return _success_response(MODEL_2, "fallback")

    service = _service(repo, handler, [MODEL_1, MODEL_2])
    assert await service.generate_text("Hello there", AUTHORIZED_USER) == "fallback"
    assert calls == 2
