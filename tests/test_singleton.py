import fcntl
import os
import tempfile
from pathlib import Path
from unittest import mock

from app.run_singleton import acquire_lock


def test_acquire_lock_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = Path(tmpdir) / "nova.lock"
        fd = acquire_lock(lock_file)
        assert fd is not None
        assert fd > 0
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_acquire_lock_already_locked():
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = Path(tmpdir) / "nova.lock"
        # Open and lock it first
        fd1 = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd1, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Try to acquire lock again
        fd2 = acquire_lock(lock_file)
        assert fd2 is None

        # Clean up
        fcntl.flock(fd1, fcntl.LOCK_UN)
        os.close(fd1)
