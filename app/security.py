"""Access-control helpers and shared security patterns for NOVA AI Office."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Shared sensitive-content pattern
# ---------------------------------------------------------------------------
# Single authoritative definition used by both memory.services and
# execution.service.  Import this symbol rather than maintaining a local copy.

SENSITIVE_CONTENT_PATTERN = re.compile(
    r"(?:telegram_bot_token|api[_ -]?key|password|credential|secret|"
    r"authorization\s*:|bearer\s+|begin [a-z ]*private key|"
    r"^\s*[A-Z][A-Z0-9_]{2,}\s*=)",
    re.IGNORECASE | re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Authorization helper
# ---------------------------------------------------------------------------


def is_authorized_user(user_id: int | None, allowed_user_id: int) -> bool:
    """Return whether an incoming Telegram user is the configured NOVA user."""
    return isinstance(user_id, int) and not isinstance(user_id, bool) and user_id == allowed_user_id
