"""Static registry of 9Router models supported by the provider gateway."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegisteredModel:
    """Metadata used to validate and select an allowlisted provider model.

    ``model_id`` is the stable NOVA-internal route alias (e.g. ``nova-v1``)
    used throughout selection, fallback, and audit. It is never sent to an
    upstream provider directly. ``upstream_route_id`` is the distinct,
    provider-specific route/combo identity (e.g. 9Router's ``general``
    combo) that the adapter actually dispatches to -- see Sprint 5G.1
    (``docs/SPRINT_5G1.md``) for why these must never be conflated.

    A ``None`` upstream_route_id means this alias has no evidenced or
    configured upstream mapping. Sprint 5G.1 deliberately leaves several
    aliases unmapped by default (see that document) rather than guessing an
    upstream identity -- an unmapped 9Router-routed alias is excluded from
    the resolved chain at selection time instead of being sent upstream as
    a literal NOVA alias, which is the exact defect this sprint fixes.
    """

    model_id: str
    provider_id: str
    supported_roles: frozenset[str]
    supported_workflows: frozenset[str]
    priority: int
    enabled: bool
    fallback_group: str
    upstream_route_id: str | None = None


_CORE_ROLES = frozenset(
    {"CONTROL_TOWER", "TECHNICAL_ARCHITECT", "WORKSPACE_KNOWLEDGE", "EXECUTION_WORKER"}
)
_CORE_WORKFLOWS = frozenset(
    {"GENERAL", "STRATEGY", "GOOGLE_WORKSPACE", "TECHNICAL", "PRESENTATION", "ACADEMIC"}
)

_MODELS = (
    RegisteredModel(
        model_id="codex-direct",
        provider_id="Codex",
        supported_roles=frozenset({"EXECUTION_WORKER"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=1,
        enabled=True,
        fallback_group="coding_direct",
    ),
    RegisteredModel(
        model_id="claude-direct",
        provider_id="Claude",
        supported_roles=frozenset({"TECHNICAL_ARCHITECT"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=1,
        enabled=True,
        fallback_group="review_direct",
    ),
    RegisteredModel(
        model_id="nova-v1",
        provider_id="9Router",
        supported_roles=_CORE_ROLES,
        supported_workflows=_CORE_WORKFLOWS,
        priority=10,
        enabled=True,
        fallback_group="core",
        # Runtime-evidenced (Sprint 5G.1): POST /v1/chat/completions with
        # model="general" -> HTTP 200, resolved upstream model gemini-pro-default.
        upstream_route_id="general",
    ),
    RegisteredModel(
        model_id="nova-v1-fallback",
        provider_id="9Router",
        supported_roles=_CORE_ROLES,
        supported_workflows=_CORE_WORKFLOWS,
        priority=20,
        enabled=True,
        fallback_group="core",
        # Deliberate exception to the "leave unmapped rather than fabricate"
        # default (unlike nova-v1-coding-fallback/nova-v1-review-fallback
        # below): nova-v1-fallback predates Sprint 5G (it is part of the
        # original Sprint 4A/4B NOVA_PROVIDER_MODEL_PRIORITY chain) and
        # existing, pre-5G tests and deployed behavior already depend on it
        # participating in the generic fallback chain. There is no evidenced
        # *distinct* upstream combo for a "general fallback", so rather than
        # silently disabling long-standing behavior, this points at the same
        # runtime-evidenced "general" combo as nova-v1 -- a real, confirmed
        # route (not a fabricated identity), preserving NOVA-side retry
        # semantics (a fresh HTTP attempt, its own circuit-breaker key) on
        # top of whatever server-side fallback "general" itself performs.
        # Sprint 5G.1's brand-new coding/review fallback aliases have no
        # such precedent and are intentionally left unmapped instead.
        upstream_route_id="general",
    ),
    RegisteredModel(
        model_id="nova-v1-fast",
        provider_id="9Router",
        supported_roles=frozenset({"FAST_ROUTER", "CONTROL_TOWER", "WORKSPACE_KNOWLEDGE"}),
        supported_workflows=frozenset({"FAST", "GENERAL"}),
        priority=30,
        enabled=True,
        fallback_group="core",
        # "Fast" is runtime-confirmed to exist on 9Router (GET /v1/models).
        # Not curl-verified end-to-end like "general"/"Development"/"review",
        # but the name-tier pairing with NOVA's own "fast" alias is the only
        # justified inference available -- unlike assigning "Fast" to coding
        # or review, which the spec explicitly forbids as arbitrary.
        upstream_route_id="Fast",
    ),
    RegisteredModel(
        model_id="nova-v2-preview",
        provider_id="9Router",
        supported_roles=frozenset({"CONTROL_TOWER"}),
        supported_workflows=frozenset({"GENERAL"}),
        priority=40,
        enabled=False,
        fallback_group="preview",
        upstream_route_id=None,
    ),
    RegisteredModel(
        model_id="nova-v1-isolated",
        provider_id="9Router",
        supported_roles=frozenset({"CONTROL_TOWER"}),
        supported_workflows=frozenset({"GENERAL"}),
        priority=50,
        enabled=True,
        fallback_group="isolated",
        # No evidenced 9Router combo corresponds to "isolated" -- left
        # unmapped rather than fabricated; excluded from any resolved chain
        # until configured.
        upstream_route_id=None,
    ),
    RegisteredModel(
        model_id="nova-v1-coding",
        provider_id="9Router",
        supported_roles=frozenset({"EXECUTION_WORKER"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=10,
        enabled=True,
        fallback_group="coding_combo",
        # Runtime-evidenced (Sprint 5G.1): GET /v1/models lists "Development".
        upstream_route_id="Development",
    ),
    RegisteredModel(
        model_id="nova-v1-coding-fallback",
        provider_id="9Router",
        supported_roles=frozenset({"EXECUTION_WORKER"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=20,
        enabled=True,
        fallback_group="coding_combo",
        # No distinct evidenced "coding fallback" combo -- left unmapped
        # rather than pointing at "Development" again (a meaningless
        # duplicate call) or fabricating an unverified identity.
        upstream_route_id=None,
    ),
    RegisteredModel(
        model_id="nova-v1-review",
        provider_id="9Router",
        supported_roles=frozenset({"TECHNICAL_ARCHITECT"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=10,
        enabled=True,
        fallback_group="review_combo",
        # Runtime-evidenced (Sprint 5G.1): GET /v1/models lists "review".
        upstream_route_id="review",
    ),
    RegisteredModel(
        model_id="nova-v1-review-fallback",
        provider_id="9Router",
        supported_roles=frozenset({"TECHNICAL_ARCHITECT"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=20,
        enabled=True,
        fallback_group="review_combo",
        # No distinct evidenced "review fallback" combo -- left unmapped for
        # the same reason as nova-v1-coding-fallback above.
        upstream_route_id=None,
    ),
)

MODEL_REGISTRY: dict[str, RegisteredModel] = {model.model_id: model for model in _MODELS}


def get_registered_model(model_id: str) -> RegisteredModel | None:
    """Return a registered model by ID, or ``None`` if it is unknown."""
    return MODEL_REGISTRY.get(model_id)
