"""Singleton wrapper for NOVA local Telegram bot entry point."""

from __future__ import annotations

import fcntl
import logging
import os
import sys
from pathlib import Path

from app.main import main


def acquire_lock(lock_path: Path) -> int | None:
    """Acquire an exclusive lock on the file descriptor."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        print(f"Error opening lock file {lock_path}: {e}", file=sys.stderr)
        return None

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None
    except OSError as e:
        print(f"Error acquiring lock on {lock_path}: {e}", file=sys.stderr)
        os.close(fd)
        return None


if __name__ == "__main__":
    # Configure root logger to output to stderr for singleton script errors
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Hardcoded relative to repo root: data/nova.lock
    repo_root = Path(__file__).resolve().parent.parent
    lock_file = repo_root / "data" / "nova.lock"

    fd = acquire_lock(lock_file)
    if fd is None:
        logger.error(f"NOVA is already running. Could not acquire lock at {lock_file}")
        sys.exit(1)

    try:
        sys.exit(main())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
