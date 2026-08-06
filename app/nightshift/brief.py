"""Brief helpers kept separate for future Telegram formatting work."""

from __future__ import annotations

from app.nightshift.models import MorningBrief


def telegram_delivery_payload(brief: MorningBrief) -> None:
    """Reserved Sprint 5B interface; delivery is intentionally unavailable."""
    del brief
    return None
