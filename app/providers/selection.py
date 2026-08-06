"""Deterministic model selection and fallback eligibility policy."""

from __future__ import annotations

from collections.abc import Callable

from app.providers.registry import get_registered_model


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
