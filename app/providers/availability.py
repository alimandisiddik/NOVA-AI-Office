"""Side-effect-free provider availability helpers."""

from __future__ import annotations

from pathlib import Path


def configured_executable(path: str) -> bool:
    """Return whether a specialist adapter has an explicit configured path."""
    return bool(path.strip())


def executable_is_available(path: str) -> bool:
    """Check one configured executable path without probing or invoking it."""
    if not configured_executable(path):
        return False
    executable = Path(path)
    return executable.is_file() and executable.stat().st_mode & 0o111 != 0
