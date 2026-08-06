"""Scope registry for Google Workspace integration with least-privilege principles."""

from __future__ import annotations

import enum


class GoogleScope(enum.Enum):
    """Google Workspace scopes allowed by NOVA AI Office."""
    # Read-only minimal scopes for identity
    USERINFO_EMAIL = "https://www.googleapis.com/auth/userinfo.email"
    USERINFO_PROFILE = "https://www.googleapis.com/auth/userinfo.profile"

    # Read-only calendar scopes
    CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"


class ScopeBundle(enum.Enum):
    """Expose approved named scope bundles through a stable accessor."""
    DEFAULT = (GoogleScope.USERINFO_EMAIL.value, GoogleScope.USERINFO_PROFILE.value)
    CALENDAR = (*DEFAULT, GoogleScope.CALENDAR_READONLY.value)


def canonicalize_scopes(scopes: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Validate, deduplicate, and sort requested scopes deterministically.

    Rejects empty scopes and unknown scope strings.
    """
    if not scopes:
        raise ValueError("Scope list cannot be empty")

    allowed = {scope.value for scope in GoogleScope}
    canonical = set()
    for scope in scopes:
        if scope not in allowed:
            raise ValueError(f"Unknown or unapproved scope requested: {scope}")
        canonical.add(scope)

    return tuple(sorted(list(canonical)))


def get_scope_bundle(bundle: ScopeBundle | str) -> tuple[str, ...]:
    """Return an immutable approved scope bundle by stable enum name."""
    try:
        selected = bundle if isinstance(bundle, ScopeBundle) else ScopeBundle[bundle]
    except KeyError as error:
        raise ValueError("Unknown approved scope bundle") from error
    return tuple(selected.value)
