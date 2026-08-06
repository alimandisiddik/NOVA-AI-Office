"""Deterministic scheduler boundary for a future runtime tick registration."""

from __future__ import annotations

from datetime import datetime

from app.nightshift.service import NightShiftService


def tick(service: NightShiftService, now: datetime) -> None:
    """Run one side-effect-limited schedule evaluation; no background loop."""
    service.tick(now)
