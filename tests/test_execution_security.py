"""Security tests for Sprint 3 execution orchestration."""

from __future__ import annotations

import re
import pytest

from app.memory.database import MemoryDatabase
from app.execution.service import ExecutionService, SensitiveContentError, SENSITIVE_CONTENT_PATTERN
from app.execution.repository import ExecutionRepository, ApprovalError


AUTHORIZED_USER = 111
UNAUTHORIZED_USER = 999

_SENSITIVE_INPUTS = [
    "My TELEGRAM_BOT_TOKEN=abc123secret",
    "api_key=supersecret_value",
    "password=hunter2",
    "Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig",
    "BEGIN RSA PRIVATE KEY",
    "credential=mysecret",
    "secret=mysecret",
    "Authorization: Bearer token123",
]

_SAFE_INPUTS = [
    "Review the architecture document",
    "Create a slide deck for Q3",
    "List all projects",
    "debug the python error in the module",
]


@pytest.fixture
def svc(tmp_path) -> ExecutionService:
    db = MemoryDatabase(tmp_path / "sec.db")
    db.initialize()
    service = ExecutionService(db, AUTHORIZED_USER)
    service.initialize()
    return service


@pytest.fixture
def repo(tmp_path) -> tuple[ExecutionRepository, MemoryDatabase]:
    db = MemoryDatabase(tmp_path / "repo.db")
    db.initialize()
    r = ExecutionRepository(db)
    r.initialize()
    return r, db


# ---------------------------------------------------------------------------
# Sensitive input guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sensitive", _SENSITIVE_INPUTS)
def test_sensitive_input_raises_before_persistence(sensitive: str, svc) -> None:
    with pytest.raises(SensitiveContentError):
        svc.submit(sensitive, AUTHORIZED_USER)


@pytest.mark.parametrize("sensitive", _SENSITIVE_INPUTS)
def test_sensitive_raw_value_absent_from_executions_table(
    sensitive: str, tmp_path
) -> None:
    db = MemoryDatabase(tmp_path / f"s_{abs(hash(sensitive))}.db")
    db.initialize()
    service = ExecutionService(db, AUTHORIZED_USER)
    service.initialize()

    try:
        service.submit(sensitive, AUTHORIZED_USER)
    except SensitiveContentError:
        pass  # expected

    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM executions").fetchall()
    for row in rows:
        for col in row.keys():
            assert sensitive not in str(row[col]), (
                f"Sensitive value found in executions.{col}"
            )


@pytest.mark.parametrize("sensitive", _SENSITIVE_INPUTS)
def test_sensitive_raw_value_absent_from_audit_log(
    sensitive: str, tmp_path
) -> None:
    db = MemoryDatabase(tmp_path / f"a_{abs(hash(sensitive))}.db")
    db.initialize()
    service = ExecutionService(db, AUTHORIZED_USER)
    service.initialize()

    try:
        service.submit(sensitive, AUTHORIZED_USER)
    except SensitiveContentError:
        pass

    with db.connection() as conn:
        rows = conn.execute("SELECT * FROM execution_audit_log").fetchall()
    for row in rows:
        for col in row.keys():
            assert sensitive not in str(row[col]), (
                f"Sensitive value found in audit_log.{col}"
            )


def test_sensitive_rejection_message_does_not_echo_raw_value(svc) -> None:
    raw = "api_key=supersecret_value"
    try:
        svc.submit(raw, AUTHORIZED_USER)
        pytest.fail("Expected SensitiveContentError")
    except SensitiveContentError as exc:
        assert raw not in str(exc), "Raw sensitive value leaked in exception message"
        assert "api_key=supersecret_value" not in str(exc)


# ---------------------------------------------------------------------------
# Unauthorized approval
# ---------------------------------------------------------------------------


def test_unauthorized_approval_rejected(svc) -> None:
    record = svc.submit("send the file to the team", AUTHORIZED_USER)
    assert record.state == "awaiting_approval"
    with pytest.raises(ApprovalError):
        svc.approve(record.id, UNAUTHORIZED_USER)


def test_unauthorized_approval_does_not_change_state(svc) -> None:
    record = svc.submit("send the file to the team", AUTHORIZED_USER)
    assert record.state == "awaiting_approval"
    try:
        svc.approve(record.id, UNAUTHORIZED_USER)
    except ApprovalError:
        pass
    refreshed = svc.get_status(record.id)
    assert refreshed.state == "awaiting_approval"


# ---------------------------------------------------------------------------
# Approval is bound to one execution ID
# ---------------------------------------------------------------------------


def test_approval_bound_to_one_execution_id(svc) -> None:
    rec_a = svc.submit("send the report", AUTHORIZED_USER)
    rec_b = svc.submit("delete the log file", AUTHORIZED_USER)
    assert rec_a.state == "awaiting_approval"
    assert rec_b.state == "awaiting_approval"

    svc.approve(rec_a.id, AUTHORIZED_USER)
    # rec_b must still be awaiting_approval
    assert svc.get_status(rec_b.id).state == "awaiting_approval"


def test_approval_non_reusable(svc) -> None:
    record = svc.submit("send the report", AUTHORIZED_USER)
    svc.approve(record.id, AUTHORIZED_USER)
    # Second approval must fail — execution is no longer in awaiting_approval
    with pytest.raises(ApprovalError):
        svc.approve(record.id, AUTHORIZED_USER)


# ---------------------------------------------------------------------------
# No shell, subprocess, network, or provider
# ---------------------------------------------------------------------------


def test_no_subprocess_in_adapter(svc) -> None:
    import subprocess as _sp
    import sys
    original_popen = _sp.Popen

    called = []

    class BlockedPopen:
        def __init__(self, *args, **kwargs):
            called.append(True)
            raise AssertionError("subprocess.Popen must not be called by the adapter")

    _sp.Popen = BlockedPopen  # type: ignore[assignment]
    try:
        record = svc.submit("Review the architecture", AUTHORIZED_USER)
    finally:
        _sp.Popen = original_popen  # type: ignore[assignment]

    assert not called, "adapter called subprocess.Popen"


def test_no_os_system_in_adapter(svc) -> None:
    import os as _os
    original = _os.system
    called = []

    def blocked(*args, **kwargs):
        called.append(True)
        raise AssertionError("os.system must not be called by the adapter")

    _os.system = blocked  # type: ignore[assignment]
    try:
        svc.submit("debug the python code", AUTHORIZED_USER)
    finally:
        _os.system = original  # type: ignore[assignment]

    assert not called


# ---------------------------------------------------------------------------
# Path traversal impossible
# ---------------------------------------------------------------------------


def test_path_not_derived_from_instruction_text(svc) -> None:
    """Execution IDs are integers; instruction text must not appear in any path."""
    record = svc.submit("create a file at /etc/passwd", AUTHORIZED_USER)
    # The dangerous path string must not be stored anywhere accessible
    with svc._repo._db.connection() as conn:
        rows = conn.execute("SELECT * FROM executions WHERE id = ?", (record.id,)).fetchall()
    for row in rows:
        for col in row.keys():
            assert "/etc/passwd" not in str(row[col]), (
                f"User path found in executions.{col}"
            )
