"""Deterministic model selection and fallback eligibility policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from dataclasses import dataclass

from app.providers.registry import RegisteredModel, get_registered_model


@dataclass(frozen=True)
class ProviderCandidate:
    provider_id: str
    model_id: str
    # The upstream/provider route identity actually sent to the adapter --
    # distinct from ``model_id`` (the NOVA-internal alias). ``None`` for
    # providers where this concept does not apply (e.g. the Codex/Claude
    # direct stubs, which are not 9Router-routed).
    upstream_route_id: str | None = None


def resolve_upstream_route_id(model: RegisteredModel, overrides: Mapping[str, str]) -> str | None:
    """Resolve the effective upstream route for a registered model.

    A configured override always wins over the registry default, so an
    operator can repoint or add a mapping (e.g. if 9Router renames a combo)
    without a code change. Returns ``None`` when neither is set -- callers
    must treat that as "not configured" for any provider that requires a
    real upstream identity, never fall back to sending the internal alias.
    """
    override = overrides.get(model.model_id)
    return override if override else model.upstream_route_id


def select_eligible_models(
    workflow_id: str,
    role_id: str,
    model_priority: list[str],
    allowed_models: list[str],
    is_circuit_open: Callable[[str], bool],
) -> list[str]:
    """Return models eligible for this request in configured priority order.

    The environment priority list is authoritative.  Eligibility filters preserve
    that order, remove duplicate IDs, and pin all fallback candidates to the
    fallback group of the first eligible model.
    """
    selected: list[str] = []
    seen_model_ids: set[str] = set()
    fallback_group: str | None = None

    for model_id in model_priority:
        if model_id in seen_model_ids:
            continue
        seen_model_ids.add(model_id)

        if model_id not in allowed_models or is_circuit_open(model_id):
            continue

        model = get_registered_model(model_id)
        if model is None or not model.enabled:
            continue
        if workflow_id not in model.supported_workflows:
            continue
        if role_id not in model.supported_roles:
            continue

        if fallback_group is None:
            fallback_group = model.fallback_group
        elif model.fallback_group != fallback_group:
            continue

        selected.append(model_id)

    return selected


def select_provider_chain(
    workflow_id: str,
    role_id: str,
    *,
    combo_priorities: dict[str, list[str]],
    allowed_models: list[str],
    configured_specialists: frozenset[str],
    upstream_route_overrides: Mapping[str, str] | None = None,
) -> list[ProviderCandidate]:
    """Resolve the deterministic cross-provider chain for one classified task."""
    overrides = upstream_route_overrides or {}
    if workflow_id == "TECHNICAL" and role_id == "EXECUTION_WORKER":
        groups = (["codex-direct"] if "Codex" in configured_specialists else []) + ["coding_or_generic"]
    elif workflow_id == "TECHNICAL" and role_id == "TECHNICAL_ARCHITECT":
        groups = (["claude-direct"] if "Claude" in configured_specialists else []) + ["review_or_generic"]
    else:
        groups = ["generic"]

    candidates: list[ProviderCandidate] = []
    seen: set[str] = set()
    for group in groups:
        priority = combo_priorities.get(group, [])
        if group in {"codex-direct", "claude-direct"}:
            priority = [group]
        elif group == "coding_or_generic":
            priority = combo_priorities.get("coding", combo_priorities.get("generic", []))
        elif group == "review_or_generic":
            priority = combo_priorities.get("review", combo_priorities.get("generic", []))
        for model_id in priority:
            if model_id in seen or model_id not in allowed_models:
                continue
            model = get_registered_model(model_id)
            if model is None or not model.enabled:
                continue
            if workflow_id not in model.supported_workflows or role_id not in model.supported_roles:
                continue
            upstream_route_id = resolve_upstream_route_id(model, overrides)
            if model.provider_id == "9Router" and not upstream_route_id:
                # No evidenced or configured upstream route for this NOVA
                # alias -- excluded rather than ever sending the internal
                # alias itself to 9Router (the Sprint 5G.1 defect). A
                # fallback alias with no distinct upstream identity simply
                # stays unreachable until an operator configures one.
                continue
            seen.add(model_id)
            candidates.append(ProviderCandidate(model.provider_id, model_id, upstream_route_id))
    return candidates
