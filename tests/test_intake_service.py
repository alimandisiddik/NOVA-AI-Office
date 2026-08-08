"""Contract coverage for Sprint 8F manual external-message intake."""

from __future__ import annotations

import ast
import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.intake.models import IntakeClassification, SOURCE_CHANNEL_WHATSAPP_MANUAL
from app.intake.service import (
    IntakeError,
    IntakeSensitiveContentError,
    IntakeService,
    IntakeStaleStateError,
)
from app.intake.telegram import waconfirm_command, wa_command
from app.memory.database import MemoryDatabase


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeMemory:
    def __init__(self) -> None:
        self.tasks: list[tuple[str, str, str]] = []
        self.notes: list[tuple[str, str]] = []

    def create_task(self, project_name: str, title: str, priority: str = "normal") -> SimpleNamespace:
        self.tasks.append((project_name, title, priority))
        return SimpleNamespace(id=len(self.tasks))

    def create_note(self, project_name: str, content: str) -> SimpleNamespace:
        self.notes.append((project_name, content))
        return SimpleNamespace(id=len(self.notes))


class FakeControlTower:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def capture_work(self, category: str, title: str, actor: str, **kwargs: object) -> SimpleNamespace:
        self.calls.append({"category": category, "title": title, "actor": actor, **kwargs})
        return SimpleNamespace(item_id="work-1")


class FakeKnowledge:
    def __init__(self) -> None:
        self.source_calls: list[dict[str, object]] = []
        self.item_calls: list[dict[str, object]] = []

    def create_source(self, title: str, source_type: str, citation_text: str, actor: str, **kwargs: object) -> SimpleNamespace:
        self.source_calls.append({"title": title, "source_type": source_type, "citation_text": citation_text, "actor": actor, **kwargs})
        return SimpleNamespace(id=31)

    def create_item(self, source_id: int, title: str, summary: str, actor: str, **kwargs: object) -> SimpleNamespace:
        self.item_calls.append({"source_id": source_id, "title": title, "summary": summary, "actor": actor, **kwargs})
        return SimpleNamespace(id=32)


def service(tmp_path: Path, *, clock: Clock | None = None, **kwargs: object) -> IntakeService:
    database = MemoryDatabase(tmp_path / "intake.sqlite3")
    database.initialize()
    intake = IntakeService(database, clock=clock, **kwargs)
    intake.initialize()
    return intake


def test_capture_persists_pending_review_and_honest_provenance(tmp_path: Path) -> None:
    intake = service(tmp_path)

    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Task: Prepare board brief", "telegram:7", "update-1")

    assert record.id == 1
    assert record.source_channel == SOURCE_CHANNEL_WHATSAPP_MANUAL
    assert record.telegram_update_id == "update-1"
    assert record.raw_text == "Task: Prepare board brief"
    assert record.classification == "task"
    assert record.status == "pending_review"
    assert record.target_id is None
    assert not hasattr(record, "whatsapp_message_id")


def test_capture_rejects_sensitive_content_before_persistence(tmp_path: Path) -> None:
    intake = service(tmp_path)

    with pytest.raises(IntakeSensitiveContentError):
        intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "API_KEY=do-not-store", "telegram:7")

    assert intake.repository.count() == 0


def test_capture_rejects_invalid_source_and_length(tmp_path: Path) -> None:
    intake = service(tmp_path)

    with pytest.raises(IntakeError):
        intake.capture("whatsapp", "Task: x", "telegram:7")
    with pytest.raises(IntakeError):
        intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "x" * 4001, "telegram:7")
    assert intake.repository.count() == 0


def test_telegram_update_id_is_permanent_delivery_idempotency_key(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 8, 8, tzinfo=UTC))
    intake = service(tmp_path, clock=clock)
    first = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Task: First", "telegram:7", "delivery-88")
    clock.value += timedelta(days=4)

    duplicate = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Knowledge: changed retry payload", "telegram:7", "delivery-88")

    assert duplicate == first
    assert intake.repository.count() == 1


def test_identical_content_is_deduplicated_only_within_scoped_window(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 8, 8, 9, 0, tzinfo=UTC))
    intake = service(tmp_path, clock=clock)
    first = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Note: acknowledged", "telegram:7")
    clock.value += timedelta(minutes=4)
    accidental_duplicate = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Note: acknowledged", "telegram:7")
    clock.value += timedelta(minutes=6)
    distinct_message = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Note: acknowledged", "telegram:7")

    assert accidental_duplicate.id == first.id
    assert distinct_message.id != first.id
    assert intake.repository.count() == 2


def test_fingerprint_dedup_window_is_not_scoped_by_actor(tmp_path: Path) -> None:
    """Documents current global (not per-actor) fingerprint scope; safe today since NOVA has exactly one authorized Telegram user."""
    clock = Clock(datetime(2026, 8, 8, 9, 0, tzinfo=UTC))
    intake = service(tmp_path, clock=clock)
    first = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Note: acknowledged", "telegram:7")

    same_window_other_actor = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Note: acknowledged", "telegram:999")

    assert same_window_other_actor.id == first.id
    assert intake.repository.count() == 1


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Task: Send the brief", "task"),
        ("Note: Customer prefers Friday", "note"),
        ("Knowledge: The policy changed", "knowledge"),
        ("Follow up: Ask finance", "follow_up"),
    ],
)
def test_deterministic_classification_labels(tmp_path: Path, text: str, label: str) -> None:
    intake = service(tmp_path)

    result = intake.classify(text)

    assert result.label == label
    assert result.confidence == "deterministic"


def test_uncertain_uses_optional_llm_only_after_deterministic_fallback(tmp_path: Path) -> None:
    fallback = service(tmp_path)
    assert fallback.classify("Maybe deal with this").label == "uncertain"

    calls: list[str] = []

    def fake_llm(text: str) -> IntakeClassification:
        calls.append(text)
        return IntakeClassification("task", "deterministic", {"title": "LLM suggestion"})

    assisted = service(tmp_path / "assisted", llm_classifier=fake_llm)
    assert assisted.classify("Maybe deal with this").confidence == "llm_assisted"
    assert calls == ["Maybe deal with this"]
    assert assisted.classify("Task: Deterministic").confidence == "deterministic"
    assert calls == ["Maybe deal with this"]


def test_confirm_uses_control_tower_public_capture_seam(tmp_path: Path) -> None:
    tower = FakeControlTower()
    intake = service(tmp_path, control_tower=tower)
    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Task: Prepare brief", "telegram:7")

    confirmed = intake.confirm(record.id, "work_item", "telegram:7", category="document", title="Prepare brief")

    assert confirmed.status == "confirmed"
    assert confirmed.target_type == "work_item"
    assert confirmed.target_id == "work-1"
    assert tower.calls == [{"category": "document", "title": "Prepare brief", "actor": "telegram:7", "summary": "Task: Prepare brief"}]


def test_confirm_uses_memory_note_public_seam(tmp_path: Path) -> None:
    memory = FakeMemory()
    intake = service(tmp_path, memory=memory)
    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Note: captured detail", "telegram:7")

    confirmed = intake.confirm(record.id, "note", "telegram:7", project_name="NOVA")

    assert confirmed.target_type == "note"
    assert confirmed.target_id == "1"
    assert memory.notes == [("NOVA", "Note: captured detail")]


def test_confirm_uses_knowledge_public_seams_with_opaque_local_origin(tmp_path: Path) -> None:
    knowledge = FakeKnowledge()
    intake = service(tmp_path, knowledge=knowledge)
    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Knowledge: policy decision", "telegram:7")

    confirmed = intake.confirm(record.id, "knowledge_item", "telegram:7", title="Policy decision")

    assert confirmed.target_type == "knowledge_item"
    assert confirmed.target_id == "32"
    assert knowledge.source_calls[0]["origin_system"] == "telegram"
    assert knowledge.source_calls[0]["origin_ref"] == f"intake:{record.id}"
    assert knowledge.item_calls[0]["source_id"] == 31
    assert knowledge.item_calls[0]["summary"] == "Knowledge: policy decision"


def test_invalid_target_and_terminal_state_fail_closed(tmp_path: Path) -> None:
    tower = FakeControlTower()
    intake = service(tmp_path, control_tower=tower)
    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Task: Do it", "telegram:7")

    with pytest.raises(IntakeError):
        intake.confirm(record.id, "follow_up", "telegram:7")
    assert tower.calls == []
    confirmed = intake.confirm(record.id, "work_item", "telegram:7")
    with pytest.raises(IntakeStaleStateError):
        intake.confirm(confirmed.id, "work_item", "telegram:7")
    with pytest.raises(IntakeStaleStateError):
        intake.dismiss(confirmed.id, "telegram:7", "not needed")


class RaisingControlTower:
    """Simulates a real ControlTowerService rejecting an invalid category."""

    def capture_work(self, *args: object, **kwargs: object) -> SimpleNamespace:
        raise RuntimeError("Category requires clarification")


def test_confirm_sanitizes_downstream_domain_validation_errors(tmp_path: Path) -> None:
    intake = service(tmp_path, control_tower=RaisingControlTower())
    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Task: Do it", "telegram:7")

    with pytest.raises(IntakeError) as excinfo:
        intake.confirm(record.id, "work_item", "telegram:7", category="bogus")

    assert not isinstance(excinfo.value, RuntimeError)
    assert intake.repository.get(record.id).status == "pending_review"


def test_dismiss_transitions_only_pending_intake(tmp_path: Path) -> None:
    intake = service(tmp_path)
    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Note: ignore", "telegram:7")

    dismissed = intake.dismiss(record.id, "telegram:7", "Not relevant")

    assert dismissed.status == "dismissed"
    with pytest.raises(IntakeStaleStateError):
        intake.dismiss(record.id, "telegram:7", "Again")


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, *, update_id: int = 101, user_id: int = 7) -> None:
        self.update_id = update_id
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_message = FakeMessage()


def telegram_context(
    intake: IntakeService | None, args: list[str], *, include_settings: bool = True
) -> SimpleNamespace:
    bot_data: dict[str, object] = {}
    if include_settings:
        bot_data["settings"] = SimpleNamespace(telegram_allowed_user_id=7)
    if intake is not None:
        bot_data["intake"] = intake
    return SimpleNamespace(args=args, bot_data=bot_data)


def run_wa(intake: IntakeService | None, args: list[str], *, include_settings: bool = True, user_id: int = 7) -> str:
    update = FakeUpdate(user_id=user_id)
    asyncio.run(wa_command(update, telegram_context(intake, args, include_settings=include_settings)))
    return update.effective_message.replies[-1]


def run_waconfirm(
    intake: IntakeService | None, args: list[str], *, include_settings: bool = True, user_id: int = 7
) -> str:
    update = FakeUpdate(user_id=user_id)
    asyncio.run(waconfirm_command(update, telegram_context(intake, args, include_settings=include_settings)))
    return update.effective_message.replies[-1]


def test_wa_handler_degrades_safely_and_requires_text(tmp_path: Path) -> None:
    assert "unavailable" in run_wa(None, ["Task:", "x"]).lower()
    assert "Usage: /wa" in run_wa(service(tmp_path), [])


def test_wa_handler_captures_text_but_never_auto_commits(tmp_path: Path) -> None:
    tower = FakeControlTower()
    intake = service(tmp_path, control_tower=tower)

    reply = run_wa(intake, ["Task:", "Prepare", "brief"])

    assert "Classification (inference): TASK" in reply
    assert "Nothing was created yet" in reply
    assert intake.repository.count() == 1
    assert tower.calls == []


def test_wa_handler_rejects_sensitive_content_and_asks_when_uncertain(tmp_path: Path) -> None:
    intake = service(tmp_path)

    sensitive = run_wa(intake, ["secret", "information"])
    uncertain = run_wa(intake, ["Please", "consider", "this"])

    assert "sensitive content" in sensitive.lower()
    assert intake.repository.count() == 1
    assert "Classification is uncertain" in uncertain


def test_wa_handler_fails_closed_when_settings_are_unavailable(tmp_path: Path) -> None:
    intake = service(tmp_path)

    reply = run_wa(intake, ["Task:", "x"], include_settings=False)

    assert "Akses ditolak" in reply
    assert intake.repository.count() == 0


def test_wa_handler_rejects_unauthorized_user(tmp_path: Path) -> None:
    intake = service(tmp_path)

    reply = run_wa(intake, ["Task:", "x"], user_id=999)

    assert "Akses ditolak" in reply
    assert intake.repository.count() == 0


def test_waconfirm_handler_promotes_via_confirm_seam_only(tmp_path: Path) -> None:
    tower = FakeControlTower()
    intake = service(tmp_path, control_tower=tower)
    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Task: Prepare brief", "telegram:7")

    reply = run_waconfirm(intake, [str(record.id), "work_item", "category=document|title=Prepare", "brief"])

    assert f"Confirmed external message #{record.id}" in reply
    assert "work_item" in reply
    assert tower.calls == [
        {"category": "document", "title": "Prepare brief", "actor": "7", "summary": "Task: Prepare brief"}
    ]
    assert intake.repository.get(record.id).status == "confirmed"


def test_waconfirm_handler_rejects_stale_and_invalid_state(tmp_path: Path) -> None:
    tower = FakeControlTower()
    intake = service(tmp_path, control_tower=tower)
    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Task: Prepare brief", "telegram:7")

    first = run_waconfirm(intake, [str(record.id), "work_item"])
    assert f"Confirmed external message #{record.id}" in first

    second = run_waconfirm(intake, [str(record.id), "work_item"])
    assert "no longer pending" in second
    assert len(tower.calls) == 1


def test_waconfirm_handler_degrades_safely_and_validates_input(tmp_path: Path) -> None:
    intake = service(tmp_path)
    record = intake.capture(SOURCE_CHANNEL_WHATSAPP_MANUAL, "Note: acknowledged", "telegram:7")

    assert "unavailable" in run_waconfirm(None, [str(record.id), "note"]).lower()
    assert "Usage: /waconfirm" in run_waconfirm(intake, [str(record.id)])
    assert "Usage: /waconfirm" in run_waconfirm(intake, ["not-an-id", "note"])
    assert "Akses ditolak" in run_waconfirm(intake, [str(record.id), "note"], include_settings=False)
    assert "Confirmation failed" in run_waconfirm(intake, [str(record.id), "unsupported_type"])
    assert intake.repository.get(record.id).status == "pending_review"


def test_schema_is_additive_and_has_partial_telegram_unique_index(tmp_path: Path) -> None:
    intake = service(tmp_path)

    with intake.database.connection() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {row[1] for row in connection.execute("PRAGMA index_list('external_message_intake')")}

    assert {"external_message_intake", "intake_audit_log"} <= tables
    assert "idx_intake_telegram_update" in indexes


def test_intake_modules_have_no_network_client_imports() -> None:
    root = Path(__file__).parents[1] / "app" / "intake"
    forbidden_roots = {"requests", "httpx", "aiohttp", "socket", "urllib", "whatsapp"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert all(name.split(".")[0] not in forbidden_roots for name in names), path


def test_no_shared_bootstrap_rewrite_in_feature_diff() -> None:
    # This branch deliberately leaves G1-owned `app/main.py` and application composition untouched.
    assert not (Path(__file__).parents[1] / "app" / "intake" / "main.py").exists()
