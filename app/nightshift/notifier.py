"""Notification routing policy with no delivery side effects."""

from __future__ import annotations


def route_notification(severity: str) -> str:
    """Map a validated severity to persisted delivery eligibility."""
    routes = {
        "informational": "morning_brief",
        "attention_required": "prioritized_morning_brief",
        "critical": "immediate_eligible",
    }
    try:
        return routes[severity]
    except KeyError as error:
        raise ValueError("Invalid notification severity") from error
