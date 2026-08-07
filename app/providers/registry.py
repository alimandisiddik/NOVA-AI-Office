"""Static registry of 9Router models supported by the provider gateway."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegisteredModel:
    """Metadata used to validate and select an allowlisted provider model."""

    model_id: str
    provider_id: str
    supported_roles: frozenset[str]
    supported_workflows: frozenset[str]
    priority: int
    enabled: bool
    fallback_group: str


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
    ),
    RegisteredModel(
        model_id="nova-v1-fallback",
        provider_id="9Router",
        supported_roles=_CORE_ROLES,
        supported_workflows=_CORE_WORKFLOWS,
        priority=20,
        enabled=True,
        fallback_group="core",
    ),
    RegisteredModel(
        model_id="nova-v1-fast",
        provider_id="9Router",
        supported_roles=frozenset({"FAST_ROUTER", "CONTROL_TOWER", "WORKSPACE_KNOWLEDGE"}),
        supported_workflows=frozenset({"FAST", "GENERAL"}),
        priority=30,
        enabled=True,
        fallback_group="core",
    ),
    RegisteredModel(
        model_id="nova-v2-preview",
        provider_id="9Router",
        supported_roles=frozenset({"CONTROL_TOWER"}),
        supported_workflows=frozenset({"GENERAL"}),
        priority=40,
        enabled=False,
        fallback_group="preview",
    ),
    RegisteredModel(
        model_id="nova-v1-isolated",
        provider_id="9Router",
        supported_roles=frozenset({"CONTROL_TOWER"}),
        supported_workflows=frozenset({"GENERAL"}),
        priority=50,
        enabled=True,
        fallback_group="isolated",
    ),
    RegisteredModel(
        model_id="nova-v1-coding",
        provider_id="9Router",
        supported_roles=frozenset({"EXECUTION_WORKER"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=10,
        enabled=True,
        fallback_group="coding_combo",
    ),
    RegisteredModel(
        model_id="nova-v1-coding-fallback",
        provider_id="9Router",
        supported_roles=frozenset({"EXECUTION_WORKER"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=20,
        enabled=True,
        fallback_group="coding_combo",
    ),
    RegisteredModel(
        model_id="nova-v1-review",
        provider_id="9Router",
        supported_roles=frozenset({"TECHNICAL_ARCHITECT"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=10,
        enabled=True,
        fallback_group="review_combo",
    ),
    RegisteredModel(
        model_id="nova-v1-review-fallback",
        provider_id="9Router",
        supported_roles=frozenset({"TECHNICAL_ARCHITECT"}),
        supported_workflows=frozenset({"TECHNICAL"}),
        priority=20,
        enabled=True,
        fallback_group="review_combo",
    ),
)

MODEL_REGISTRY: dict[str, RegisteredModel] = {model.model_id: model for model in _MODELS}


def get_registered_model(model_id: str) -> RegisteredModel | None:
    """Return a registered model by ID, or ``None`` if it is unknown."""
    return MODEL_REGISTRY.get(model_id)
