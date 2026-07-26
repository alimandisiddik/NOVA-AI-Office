"""Access-control helpers for NOVA AI Office."""

from __future__ import annotations


def is_authorized_user(user_id: int | None, allowed_user_id: int) -> bool:
    """Return whether an incoming Telegram user is the configured NOVA user."""
    return isinstance(user_id, int) and not isinstance(user_id, bool) and user_id == allowed_user_id
