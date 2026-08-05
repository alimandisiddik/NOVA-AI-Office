"""Tests for the provider gateway (Sprint 4A)."""

import pytest
import httpx
import json

from app.memory.database import MemoryDatabase
from app.providers.repository import ProviderRepository
from app.providers.ninerouter import NineRouterAdapter
from app.providers.service import ProviderGatewayService
from app.providers.errors import (
    AuthenticationError,
    AuthorizationError,
    CircuitOpenError,
    ConfigurationError,
    ConnectionError,
    InvalidResponseError,
    OutputLimitError,
    ProviderError,
    RateLimitError,
    SensitiveContentError,
    TimeoutError,
    UnsupportedOperationError,
)

AUTHORIZED_USER = 111

@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def memory_db(tmp_path):
    db = MemoryDatabase(tmp_path / "provider.db")
    db.initialize()
    return db

@pytest.fixture
def repo(memory_db):
    r = ProviderRepository(memory_db)
    r.initialize()
    return r

def test_configuration_validation_https_required(repo):
    with pytest.raises(ConfigurationError, match="HTTPS"):
        ProviderGatewayService(
            repo, None, "http://example.com", "key", "model-1", ["model-1"]
        )

def test_configuration_validation_localhost_http_allowed(repo):
    # Should not raise ConfigurationError
    ProviderGatewayService(
        repo, None, "http://localhost:8000", "key", "model-1", ["model-1"]
    )

def test_configuration_validation_invalid_model(repo):
    with pytest.raises(ConfigurationError, match="allowed models"):
        ProviderGatewayService(
            repo, None, "https://api.com", "key", "model-2", ["model-1"]
        )

@pytest.mark.anyio
async def test_successful_mocked_response(repo):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "req-123",
            "model": "model-1",
            "choices": [{"message": {"role": "assistant", "content": "Hello World"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
        })

    transport = httpx.MockTransport(mock_handler)
    adapter = NineRouterAdapter("https://api.com", "key", transport=transport)
    svc = ProviderGatewayService(repo, adapter, "https://api.com", "key", "model-1", ["model-1"])
    svc.initialize()

    response = await svc.generate_text("Hi", AUTHORIZED_USER)
    assert response == "Hello World"

    # Check audit log
    with repo._db.connection() as conn:
        row = conn.execute("SELECT * FROM provider_request_audit").fetchone()
    assert row is not None
    assert row["status"] == "success"
    assert row["execution_id"] is None
    assert row["provider_id"] == "9Router"
    assert row["model_id"] == "model-1"

@pytest.mark.anyio
async def test_timeout_triggers_retry_then_fails(repo):
    call_count = 0
    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.TimeoutException("timeout")

    transport = httpx.MockTransport(mock_handler)
    adapter = NineRouterAdapter("https://api.com", "key", transport=transport)
    svc = ProviderGatewayService(repo, adapter, "https://api.com", "key", "model-1", ["model-1"])
    svc.initialize()

    with pytest.raises(TimeoutError):
        await svc.generate_text("Hi", AUTHORIZED_USER)

    # MAX_RETRIES is 2, so total calls should be 3
    assert call_count == 3

    # Check audit
    with repo._db.connection() as conn:
        row = conn.execute("SELECT * FROM provider_request_audit").fetchone()
    assert row["status"] == "failed"
    assert row["error_category"] == "timeout_error"
    assert row["retry_count"] == 2

@pytest.mark.anyio
async def test_sensitive_prompt_rejected_before_network(repo):
    call_count = 0
    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(mock_handler)
    adapter = NineRouterAdapter("https://api.com", "key", transport=transport)
    svc = ProviderGatewayService(repo, adapter, "https://api.com", "key", "model-1", ["model-1"])
    svc.initialize()

    with pytest.raises(SensitiveContentError):
        await svc.generate_text("Here is my PASSWORD=hunter2", AUTHORIZED_USER)

    assert call_count == 0  # no network call

@pytest.mark.anyio
async def test_destructive_prompt_rejected_before_network(repo):
    call_count = 0
    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(mock_handler)
    adapter = NineRouterAdapter("https://api.com", "key", transport=transport)
    svc = ProviderGatewayService(repo, adapter, "https://api.com", "key", "model-1", ["model-1"])
    svc.initialize()

    with pytest.raises(AuthorizationError):
        await svc.generate_text("rm -rf /", AUTHORIZED_USER)

    assert call_count == 0  # no network call

@pytest.mark.anyio
async def test_401_no_retry(repo):
    call_count = 0
    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    transport = httpx.MockTransport(mock_handler)
    adapter = NineRouterAdapter("https://api.com", "key", transport=transport)
    svc = ProviderGatewayService(repo, adapter, "https://api.com", "key", "model-1", ["model-1"])
    svc.initialize()

    with pytest.raises(AuthenticationError):
        await svc.generate_text("Hi", AUTHORIZED_USER)

    assert call_count == 1  # No retry

@pytest.mark.anyio
async def test_circuit_breaker_opens_after_threshold(repo):
    call_count = 0
    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(500, json={"error": "server error"})

    transport = httpx.MockTransport(mock_handler)
    adapter = NineRouterAdapter("https://api.com", "key", transport=transport)
    svc = ProviderGatewayService(repo, adapter, "https://api.com", "key", "model-1", ["model-1"])
    svc.initialize()

    # Threshold is 5. Each call attempts 3 times (1 + 2 retries) = 3 failures.
    # First call -> 3 failures.
    with pytest.raises(ProviderError):
        await svc.generate_text("Hi", AUTHORIZED_USER)
    assert svc.circuit_breaker.failures == 3
    assert svc.circuit_breaker.state == "closed"

    # Second call -> 2 more failures reach threshold (5). Then circuit opens.
    # Actually, the retry loop checks `generate_text`. Let's see.
    with pytest.raises(ProviderError):
        await svc.generate_text("Hi", AUTHORIZED_USER)

    assert svc.circuit_breaker.state == "open"

    # Third call -> immediately blocked by circuit breaker before network.
    with pytest.raises(CircuitOpenError):
        await svc.generate_text("Hi", AUTHORIZED_USER)

@pytest.mark.anyio
async def test_oversized_response(repo):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": "x" * 70000}}]
        })

    transport = httpx.MockTransport(mock_handler)
    adapter = NineRouterAdapter("https://api.com", "key", transport=transport)
    svc = ProviderGatewayService(repo, adapter, "https://api.com", "key", "model-1", ["model-1"])
    svc.initialize()

    with pytest.raises(OutputLimitError):
        await svc.generate_text("Hi", AUTHORIZED_USER)

@pytest.mark.anyio
async def test_malformed_json_response(repo):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{malformed json")

    transport = httpx.MockTransport(mock_handler)
    adapter = NineRouterAdapter("https://api.com", "key", transport=transport)
    svc = ProviderGatewayService(repo, adapter, "https://api.com", "key", "model-1", ["model-1"])
    svc.initialize()

    with pytest.raises(InvalidResponseError):
        await svc.generate_text("Hi", AUTHORIZED_USER)

@pytest.mark.anyio
async def test_redirect_rejection(repo):
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"Location": "https://other.com"})

    transport = httpx.MockTransport(mock_handler)
    adapter = NineRouterAdapter("https://api.com", "key", transport=transport)
    svc = ProviderGatewayService(repo, adapter, "https://api.com", "key", "model-1", ["model-1"])
    svc.initialize()

    with pytest.raises(ConnectionError, match="Redirects are not permitted"):
        await svc.generate_text("Hi", AUTHORIZED_USER)
