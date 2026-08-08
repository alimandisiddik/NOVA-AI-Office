"""Focused contract coverage for Workspace-to-Control-Tower promotion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.control_tower.service import ControlTowerService
from app.google_workspace.drive.models import DriveFileMetadata
from app.google_workspace.calendar.dtos import MeetingBrief, TimeRange
from app.google_workspace.gmail.dtos import MessageSummary
from app.knowledge.service import KnowledgeService
from app.memory.database import MemoryDatabase
from app.workspace_bridge.service import (
    WorkspaceAccountUnavailableError,
    WorkspaceBridgeSensitiveContentError,
    WorkspaceBridgeService,
    WorkspaceBridgeStaleStateError,
    WorkspaceBridgeValidationError,
)
from app.workspace_bridge.telegram import workspacecandidates_command, workspacecommit_command


class FakeAuthenticator:
    def __init__(self, namespace: str | None = "account-a") -> None:
        self.namespace = namespace
        self.calls = 0

    def get_account_namespace(self) -> str | None:
        self.calls += 1
        return self.namespace


def _message(message_id: str, *, subject: str = "Please review the briefing") -> MessageSummary:
    return MessageSummary(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        subject=subject,
        sender_alias="sender_alias",
        received_at=datetime.now(timezone.utc),
        snippet="Safe metadata-only snippet",
        has_attachments=False,
        label_ids=("INBOX",),
    )


def _document(file_id: str, *, name: str = "Operating brief") -> DriveFileMetadata:
    return DriveFileMetadata(file_id, name, "application/pdf", 10, datetime.now(timezone.utc))


def _meeting(event_id: str | None, *, title: str = "Planning meeting") -> MeetingBrief:
    start = datetime.now(timezone.utc)
    return MeetingBrief(title, TimeRange(start, start + timedelta(hours=1)), "UTC", event_id=event_id)


def _service(tmp_path: Path) -> tuple[WorkspaceBridgeService, FakeAuthenticator, ControlTowerService, KnowledgeService]:
    database = MemoryDatabase(tmp_path / "nova.sqlite3")
    database.initialize()
    control_tower = ControlTowerService(database)
    control_tower.initialize()
    knowledge = KnowledgeService(database, control_tower=control_tower)
    knowledge.initialize()
    authenticator = FakeAuthenticator()
    bridge = WorkspaceBridgeService(database, authenticator, control_tower, knowledge)
    bridge.initialize()
    return bridge, authenticator, control_tower, knowledge


def test_message_proposal_preserves_exact_source_identity_and_deduplicates(tmp_path: Path) -> None:
    bridge, authenticator, _, _ = _service(tmp_path)

    first = bridge.propose_from_message(_message("gmail-message-42"), "7")
    repeated = bridge.propose_from_message(_message("gmail-message-42"), "7")

    assert authenticator.calls == 2
    assert first.id == repeated.id
    assert first.source_system == "gmail"
    assert first.account_namespace == "account-a"
    assert first.external_source_type == "message"
    assert first.external_source_id == "gmail-message-42"
    assert first.status == "candidate"
    assert first.target_type is None and first.target_id is None
    assert "inference" in first.candidate_summary.lower()


def test_same_text_different_ids_and_same_id_different_accounts_remain_distinct(tmp_path: Path) -> None:
    bridge, authenticator, _, _ = _service(tmp_path)

    one = bridge.propose_from_message(_message("gmail-1", subject="Same subject"), "7")
    two = bridge.propose_from_message(_message("gmail-2", subject="Same subject"), "7")
    authenticator.namespace = "account-b"
    three = bridge.propose_from_message(_message("gmail-1", subject="Same subject"), "7")

    assert len({one.id, two.id, three.id}) == 3
    assert one.content_fingerprint == two.content_fingerprint
    assert one.external_source_id == three.external_source_id
    assert one.account_namespace != three.account_namespace


def test_document_and_meeting_proposals_require_stable_ids(tmp_path: Path) -> None:
    bridge, _, _, _ = _service(tmp_path)

    document = bridge.propose_from_document(_document("drive-file-9"), "7")
    meeting = bridge.propose_from_meeting(_meeting("calendar-event-6"), "7")

    assert (document.source_system, document.external_source_id) == ("drive", "drive-file-9")
    assert (meeting.source_system, meeting.external_source_id) == ("calendar", "calendar-event-6")
    with pytest.raises(WorkspaceBridgeValidationError, match="event identity"):
        bridge.propose_from_meeting(_meeting(None), "7")


def test_same_calendar_event_deduplicates_and_distinct_events_do_not(tmp_path: Path) -> None:
    bridge, _, _, _ = _service(tmp_path)

    first = bridge.propose_from_meeting(_meeting("event-1", title="Same meeting"), "7")
    repeated = bridge.propose_from_meeting(_meeting("event-1", title="Same meeting"), "7")
    distinct = bridge.propose_from_meeting(_meeting("event-2", title="Same meeting"), "7")

    assert first.id == repeated.id
    assert first.id != distinct.id
    assert first.content_fingerprint == distinct.content_fingerprint


def test_account_unavailable_and_sensitive_candidate_fail_before_persistence(tmp_path: Path) -> None:
    bridge, authenticator, _, _ = _service(tmp_path)
    authenticator.namespace = None

    with pytest.raises(WorkspaceAccountUnavailableError):
        bridge.propose_from_message(_message("gmail-1"), "7")
    assert bridge.list_candidates() == []

    authenticator.namespace = "account-a"
    with pytest.raises(WorkspaceBridgeSensitiveContentError):
        bridge.propose_from_message(_message("gmail-2", subject="password=do-not-store"), "7")
    assert bridge.list_candidates() == []


def test_constructor_has_no_auth_or_network_side_effect(tmp_path: Path) -> None:
    bridge, authenticator, _, _ = _service(tmp_path)

    assert bridge is not None
    assert authenticator.calls == 0


def test_explicit_commit_uses_canonical_public_services_and_preserves_provenance(tmp_path: Path) -> None:
    bridge, _, control_tower, knowledge = _service(tmp_path)
    work_candidate = bridge.propose_from_message(_message("gmail-work"), "7")
    knowledge_candidate = bridge.propose_from_document(_document("drive-knowledge"), "7")

    work = bridge.commit(work_candidate.id, "work_item", "7")
    knowledge_ref = bridge.commit(knowledge_candidate.id, "knowledge_item", "7")

    assert work.status == "committed" and work.target_type == "work_item"
    assert control_tower.repository.get_work_item(work.target_id or "") is not None
    assert knowledge_ref.status == "committed" and knowledge_ref.target_type == "knowledge_item"
    result = knowledge.query(keyword="Document candidate")[0]
    assert result.source.source_type == "drive_file"
    assert result.source.origin_ref == f"workspace_source_ref:{knowledge_ref.id}"
    with pytest.raises(WorkspaceBridgeStaleStateError):
        bridge.commit(work_candidate.id, "work_item", "7")


def test_invalid_target_and_downstream_failure_leave_candidate_pending(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge, _, control_tower, _ = _service(tmp_path)
    candidate = bridge.propose_from_message(_message("gmail-pending"), "7")

    with pytest.raises(WorkspaceBridgeValidationError):
        bridge.commit(candidate.id, "note", "7")

    def fail_capture(*args: object, **kwargs: object) -> object:
        raise RuntimeError("provider detail must not escape")

    monkeypatch.setattr(control_tower, "capture_work", fail_capture)
    with pytest.raises(Exception):
        bridge.commit(candidate.id, "work_item", "7")
    assert bridge.repository.get(candidate.id).status == "candidate"  # type: ignore[union-attr]


def test_dismissal_is_terminal_and_reason_is_screened(tmp_path: Path) -> None:
    bridge, _, _, _ = _service(tmp_path)
    candidate = bridge.propose_from_message(_message("gmail-dismiss"), "7")

    dismissed = bridge.dismiss(candidate.id, "7", "Not relevant")

    assert dismissed.status == "dismissed"
    with pytest.raises(WorkspaceBridgeStaleStateError):
        bridge.dismiss(candidate.id, "7", "Still not relevant")


def test_telegram_handlers_fail_closed_and_require_explicit_target(tmp_path: Path) -> None:
    import asyncio

    bridge, _, _, _ = _service(tmp_path)
    candidate = bridge.propose_from_message(_message("gmail-telegram"), "7")

    class Message:
        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, value: str) -> None:
            self.replies.append(value)

    settings = SimpleNamespace(telegram_allowed_user_id=7)
    message = Message()
    update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=8))
    context = SimpleNamespace(bot_data={"settings": settings, "workspace_bridge": bridge}, args=[])
    asyncio.run(workspacecandidates_command(update, context))
    assert message.replies == ["Akses ditolak. NOVA adalah AI Office pribadi."]

    update.effective_user.id = 7
    asyncio.run(workspacecandidates_command(update, context))
    assert f"#{candidate.id}" in message.replies[-1]
    context.args = [str(candidate.id)]
    asyncio.run(workspacecommit_command(update, context))
    assert message.replies[-1].startswith("Usage: /workspacecommit")
    context.args = [str(candidate.id), "work_item"]
    asyncio.run(workspacecommit_command(update, context))
    assert "Committed Workspace candidate" in message.replies[-1]
