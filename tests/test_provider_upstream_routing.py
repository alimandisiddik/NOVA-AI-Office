"""Sprint 5G.1 — NOVA internal alias vs. 9Router upstream route mapping.

Confirmed runtime evidence (see docs/SPRINT_5G1.md) proved NOVA sent its own
internal route aliases ("nova-v1", "nova-v1-coding", "nova-v1-review")
directly to 9Router's ``POST /v1/chat/completions``, which 9Router does not
recognize -- HTTP 404 -- even though the same endpoint, with a real 9Router
combo ID ("general"), returns HTTP 200. This file proves the fix: three
genuinely distinct identities now flow through selection, the wire request,
and the audit trail without ever being conflated:

  1. the NOVA-internal alias  (e.g. "nova-v1")
  2. the upstream/provider route actually dispatched to (e.g. "general")
  3. the actual resolved model 9Router reports it used (e.g. "gemini-pro-default")
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from app.memory.database import MemoryDatabase
from app.providers.errors import ConfigurationError
from app.providers.models import ProviderRequest
from app.providers.ninerouter import NineRouterAdapter
from app.providers.registry import get_registered_model
from app.providers.repository import ProviderRepository
from app.providers.selection import ProviderCandidate, resolve_upstream_route_id, select_provider_chain
from app.providers.service import ProviderGatewayService

AUTHORIZED_USER = 111


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def repository(tmp_path) -> ProviderRepository:
    database = MemoryDatabase(tmp_path / "provider-upstream.db")
    database.initialize()
    repo = ProviderRepository(database)
    repo.initialize()
    return repo


# -- 1: internal alias -> upstream route mapping (registry defaults) --------


def test_nova_v1_maps_to_general():
    model = get_registered_model("nova-v1")
    assert model is not None
    assert resolve_upstream_route_id(model, {}) == "general"


def test_coding_route_maps_to_development():
    model = get_registered_model("nova-v1-coding")
    assert model is not None
    assert resolve_upstream_route_id(model, {}) == "Development"


def test_review_route_maps_to_review():
    model = get_registered_model("nova-v1-review")
    assert model is not None
    assert resolve_upstream_route_id(model, {}) == "review"


def test_override_wins_over_registry_default():
    model = get_registered_model("nova-v1")
    assert resolve_upstream_route_id(model, {"nova-v1": "general-v2"}) == "general-v2"


def test_unmapped_fallback_alias_has_no_default_route():
    """No fabricated identity for a genuinely unevidenced combo."""
    model = get_registered_model("nova-v1-coding-fallback")
    assert model is not None
    assert resolve_upstream_route_id(model, {}) is None


# -- 2: internal alias preserved through selection --------------------------


def test_selection_preserves_internal_alias_alongside_upstream_route():
    chain = select_provider_chain(
        "TECHNICAL", "EXECUTION_WORKER",
        combo_priorities={"coding": ["nova-v1-coding"]},
        allowed_models=["nova-v1-coding"],
        configured_specialists=frozenset(),
    )
    assert chain == [ProviderCandidate("9Router", "nova-v1-coding", "Development")]
    # Both identities remain independently readable on the same candidate.
    assert chain[0].model_id == "nova-v1-coding"
    assert chain[0].upstream_route_id == "Development"


def test_unmapped_9router_candidate_is_excluded_not_sent_as_alias():
    """A NOVA alias without a configured upstream route is excluded from
    the resolved chain entirely -- it must never reach an adapter carrying
    the internal alias as if it were a real upstream identity."""
    chain = select_provider_chain(
        "TECHNICAL", "EXECUTION_WORKER",
        combo_priorities={"coding": ["nova-v1-coding-fallback"]},
        allowed_models=["nova-v1-coding-fallback"],
        configured_specialists=frozenset(),
    )
    assert chain == []


# -- 3 & 6: the adapter sends the upstream route, and resolved model is separate --


def test_adapter_sends_upstream_route_not_internal_alias():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["sent_model"] = json.loads(request.content)["model"]
        return httpx.Response(
            200,
            json={
                "model": "gemini-pro-default",
                "choices": [{"message": {"role": "assistant", "content": "NOVA_OK"}}],
            },
        )

    adapter = NineRouterAdapter("https://api.example.test", "test-key", transport=httpx.MockTransport(handler))
    request = ProviderRequest(
        request_id="r1", user_id=1, provider_id="9Router", model_id="nova-v1",
        workflow_id="GENERAL", role_id="CONTROL_TOWER", prompt="hello",
        upstream_route_id="general",
    )

    import asyncio
    response = asyncio.run(adapter.generate_text(request, timeout_seconds=5.0))

    # The wire payload carried the upstream route, never the NOVA alias.
    assert captured["sent_model"] == "general"
    assert captured["sent_model"] != "nova-v1"
    # The response's resolved model is a third, distinct identity again.
    assert response.model_id == "gemini-pro-default"
    assert response.model_id not in ("nova-v1", "general")
    assert response.content == "NOVA_OK"


def test_adapter_falls_back_to_upstream_route_when_response_omits_model():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]})

    adapter = NineRouterAdapter("https://api.example.test", "test-key", transport=httpx.MockTransport(handler))
    request = ProviderRequest(
        request_id="r1", user_id=1, provider_id="9Router", model_id="nova-v1",
        workflow_id="GENERAL", role_id="CONTROL_TOWER", prompt="hello",
        upstream_route_id="general",
    )

    import asyncio
    response = asyncio.run(adapter.generate_text(request, timeout_seconds=5.0))

    # Missing "model" in the response falls back to the upstream route,
    # never silently reports the internal alias as if it were resolved.
    assert response.model_id == "general"


# -- 7: unknown/unconfigured upstream route fails safely --------------------


def test_adapter_refuses_to_send_without_an_upstream_route():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("adapter must not make a network call without a resolved upstream route")

    adapter = NineRouterAdapter("https://api.example.test", "test-key", transport=httpx.MockTransport(handler))
    request = ProviderRequest(
        request_id="r1", user_id=1, provider_id="9Router", model_id="nova-v1-coding-fallback",
        workflow_id="TECHNICAL", role_id="EXECUTION_WORKER", prompt="hello",
        upstream_route_id=None,
    )

    import asyncio
    with pytest.raises(ConfigurationError):
        asyncio.run(adapter.generate_text(request, timeout_seconds=5.0))


@pytest.mark.anyio
async def test_gateway_fails_safely_when_only_unmapped_aliases_are_configured(repository) -> None:
    """A workflow/role whose only allowed alias has no upstream route must
    fail deterministically, not silently attempt a bogus request."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach the network with an unmapped route")

    adapter = NineRouterAdapter("https://api.example.test", "test-key", transport=httpx.MockTransport(handler))
    service = ProviderGatewayService(
        repository, adapter, "https://api.example.test", "test-key",
        ["nova-v1-coding-fallback"], ["nova-v1-coding-fallback"],
    )

    with pytest.raises(ConfigurationError, match="No eligible provider route"):
        await service.generate_text("implement module", AUTHORIZED_USER, workflow_id="TECHNICAL", role_id="EXECUTION_WORKER")


# -- Audit: all three identities are separately recorded --------------------


@pytest.mark.anyio
async def test_audit_records_all_three_distinct_identities(repository) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == "general"
        return httpx.Response(
            200,
            json={"model": "gemini-pro-default", "choices": [{"message": {"role": "assistant", "content": "NOVA_OK"}}]},
        )

    adapter = NineRouterAdapter("https://api.example.test", "test-key", transport=httpx.MockTransport(handler))
    service = ProviderGatewayService(
        repository, adapter, "https://api.example.test", "test-key", ["nova-v1"], ["nova-v1"],
    )

    result = await service.generate_text("hello", AUTHORIZED_USER, workflow_id="GENERAL", role_id="CONTROL_TOWER")
    assert result == "NOVA_OK"

    with repository._db.connection() as connection:
        audit = connection.execute("SELECT * FROM provider_request_audit ORDER BY rowid DESC LIMIT 1").fetchone()
        attempt = connection.execute(
            "SELECT * FROM provider_request_attempts WHERE request_id = ?", (audit["request_id"],)
        ).fetchone()

    assert audit["final_model_id"] == "nova-v1"                    # (1) NOVA-internal alias
    assert audit["final_upstream_route_id"] == "general"           # (2) upstream/provider route
    assert audit["resolved_model_label"] == "gemini-pro-default"   # (3) actual resolved model
    assert attempt["model_id"] == "nova-v1"
    assert attempt["upstream_route_id"] == "general"
    # All three are pairwise distinct -- the exact defect this sprint fixes.
    assert len({audit["final_model_id"], audit["final_upstream_route_id"], audit["resolved_model_label"]}) == 3


# -- 8: no secrets logged -----------------------------------------------------


@pytest.mark.anyio
async def test_no_secrets_logged_on_upstream_routing_failure(repository, caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": "bad key"})

    secret_api_key = "sk-test-super-secret-9router-key"
    adapter = NineRouterAdapter("https://api.example.test", secret_api_key, transport=httpx.MockTransport(handler))
    service = ProviderGatewayService(
        repository, adapter, "https://api.example.test", secret_api_key, ["nova-v1"], ["nova-v1"],
    )

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Exception):
            await service.generate_text("hello", AUTHORIZED_USER, workflow_id="GENERAL", role_id="CONTROL_TOWER")

    for record in caplog.records:
        assert secret_api_key not in record.getMessage()
        assert "Bearer" not in record.getMessage()
