"""Telegram-facing helpers for conversation resolution responses."""

from __future__ import annotations

from app.conversation.models import Resolution


def response_text(resolution: Resolution) -> str:
    return resolution.response_text
