from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.drafting import PREPARED_WORKSPACE_ACTION_TYPES, DraftingService
from app.drafting.service import (
    DraftingSensitiveContentError,
    DraftingTransitionError,
    DraftingValidationError,
)
from app.memory.database import MemoryDatabase


@pytest.fixture
def service(tmp_path) -> DraftingService:
    drafting = DraftingService(MemoryDatabase(tmp_path / "nova.sqlite3"))
    drafting.initialize()
    return drafting


def test_all_frozen_draft_types_are_local_and_schema_shaped(service: DraftingService) -> None:
    reply = service.prepare_gmail_reply("message-1", "Thank them and request clarification.", "user:7")
    new = service.prepare_gmail_new("Introduce the proposed next step.", "user:7")
    memo = service.prepare_docs_memo("Decision memo", "Summarize the supplied proposal.", "user:7")
    sheet = service.prepare_sheets_change("sheet-1", "Q1!A1:B2", "Propose updated values.", "user:7")
    slides = service.prepare_slides_outline("Board update", "Outline the current request.", "user:7")

    assert {reply.content_type, new.content_type, memo.content_type, sheet.content_type, slides.content_type} == (
        PREPARED_WORKSPACE_ACTION_TYPES
    )
    assert all(item.status == "prepared" for item in (reply, new, memo, sheet, slides))
    assert all(item.body_text is not None for item in (reply, new, memo))
    assert sheet.body_text is None and slides.body_text is None
    assert json.loads(sheet.body_payload or "") == {
        "a1_range": "Q1!A1:B2",
        "values": [[json.loads(sheet.body_payload or "")["values"][0][0]]],
    }
    assert json.loads(slides.body_payload or "")[0]["slide_number"] == 1


def test_text_drafts_are_explicit_and_separate_facts_inference_recommendation(service: DraftingService) -> None:
    action = service.prepare_gmail_reply("message-1", "Reply with the requested status.", "user:7")

    assert action.body_text is not None
    assert action.body_text.startswith("DRAFT — LOCAL ONLY")
    assert "FACT — Source reference: message-1." in action.body_text
    assert "INFERENCE —" in action.body_text
    assert "RECOMMENDATION —" in action.body_text
    assert "DRAFTED TEXT\nReply with the requested status." in action.body_text
    assert "Nothing was sent" not in action.body_text


@pytest.mark.parametrize("method,args,error_type", [
    ("prepare_gmail_reply", ("message-1", "api_key=secret", "user:7"), DraftingSensitiveContentError),
    ("prepare_docs_memo", ("Memo", "x" * 4_001, "user:7"), DraftingValidationError),
    ("prepare_sheets_change", ("sheet-1", "A1;DROP", "Safe", "user:7"), DraftingValidationError),
])
def test_sensitive_or_unbounded_or_invalid_inputs_are_rejected(
    service: DraftingService, method: str, args: tuple[str, ...], error_type: type[Exception]
) -> None:
    with pytest.raises(error_type):
        getattr(service, method)(*args)


def test_no_source_context_and_no_generator_are_safe_and_deterministic(service: DraftingService) -> None:
    action = service.prepare_gmail_new("Prepare an invitation draft.", "user:7")

    assert action.source_ref is None
    assert action.body_text is not None
    assert "FACT — No source record was supplied." in action.body_text
    assert "unverified" in action.body_text


def test_generator_failure_degrades_to_deterministic_draft(tmp_path) -> None:
    def failing_generator(*_args: object) -> str:
        raise RuntimeError("provider unavailable")

    service = DraftingService(MemoryDatabase(tmp_path / "nova.sqlite3"), generator=failing_generator)
    service.initialize()
    action = service.prepare_docs_memo("Memo", "Use only this instruction.", "user:7")

    assert action.body_text is not None
    assert action.body_text.endswith("DRAFTED TEXT\nUse only this instruction.")


def test_revision_preserves_history_and_ready_handoff_is_explicit(service: DraftingService) -> None:
    original = service.prepare_docs_memo("Memo", "Initial text.", "user:7")
    revision = service.revise(original.id, "Revised text.", "user:7")

    persisted_original = service.get_action(original.id)
    assert persisted_original.status == "superseded"
    assert revision.supersedes_id == original.id
    assert "Initial text." in (persisted_original.body_text or "")
    assert "Revised text." in (revision.body_text or "")
    assert service.get_ready_action(revision.id) is None
    assert service.get_ready_action(original.id) is None

    ready = service.mark_ready_for_action(revision.id, "user:7")
    assert ready.status == "ready_for_action"
    assert service.get_ready_action(revision.id) == ready

    with pytest.raises(DraftingTransitionError):
        service.mark_ready_for_action(revision.id, "user:7")
    with pytest.raises(DraftingTransitionError):
        service.revise(revision.id, "No longer mutable.", "user:7")


def test_revise_rejects_stale_already_superseded_draft(service: DraftingService) -> None:
    original = service.prepare_docs_memo("Memo", "Initial text.", "user:7")
    service.revise(original.id, "Revised text.", "user:7")

    with pytest.raises(DraftingTransitionError):
        service.revise(original.id, "Attempted second revision of stale draft.", "user:7")


def test_revise_screens_sensitive_instructions_before_any_mutation(service: DraftingService) -> None:
    original = service.prepare_docs_memo("Memo", "Initial text.", "user:7")

    with pytest.raises(DraftingSensitiveContentError):
        service.revise(original.id, "api_key=secret", "user:7")

    unchanged = service.get_action(original.id)
    assert unchanged.status == "prepared"
    assert "Initial text." in (unchanged.body_text or "")


def test_generator_output_containing_sensitive_content_falls_back_safely(tmp_path) -> None:
    def leaking_generator(*_args: object) -> str:
        return "password=hunter2"

    service = DraftingService(MemoryDatabase(tmp_path / "nova.sqlite3"), generator=leaking_generator)
    service.initialize()
    action = service.prepare_docs_memo("Memo", "Use only this instruction.", "user:7")

    assert action.body_text is not None
    assert "password=hunter2" not in action.body_text
    assert action.body_text.endswith("DRAFTED TEXT\nUse only this instruction.")


def test_initialize_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "nova.sqlite3"
    first = DraftingService(MemoryDatabase(db_path))
    first.initialize()
    first.initialize()

    second = DraftingService(MemoryDatabase(db_path))
    second.initialize()
    action = second.prepare_docs_memo("Memo", "Still works after repeated init.", "user:7")

    assert action.status == "prepared"
    assert second.get_action(action.id) == action


def test_drafting_package_has_no_workspace_mutation_or_shared_bootstrap_reference() -> None:
    package = Path(__file__).parents[1] / "app" / "drafting"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))

    forbidden = (
        "googleapiclient",
        "drafts().create",
        "messages().send",
        "documents().batchUpdate",
        "spreadsheets().values().update",
        "presentations().batchUpdate",
        "app.workspace_actions",
        "build_application",
    )
    assert not any(item in source for item in forbidden)
    assert "keep_note" not in source
    assert "def prepare_keep" not in source.lower()
