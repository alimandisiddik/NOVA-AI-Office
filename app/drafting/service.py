"""Deterministic preparation of local-only Workspace content drafts."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from app.drafting.models import PREPARED_WORKSPACE_ACTION_TYPES, PreparedWorkspaceAction
from app.drafting.repository import DraftingRepository, utc_now
from app.drafting.schema import apply_schema
from app.memory.database import MemoryDatabase
from app.security import SENSITIVE_CONTENT_PATTERN


_A1_RANGE = re.compile(
    r"^(?:(?:'[^']{1,60}'|[A-Za-z_][\w ]{0,60})!)?\$?[A-Z]{1,3}\$?[1-9]\d{0,5}(?::\$?[A-Z]{1,3}\$?[1-9]\d{0,5})?$"
)
_TEXT_TYPES = frozenset({"gmail_reply", "gmail_new", "docs_memo"})
_PAYLOAD_TYPES = frozenset({"sheets_change", "slides_outline"})
_Generator = Callable[[str, str, str | None, str | None], str]


class DraftingError(Exception):
    """Safe base error for local draft preparation."""


class DraftingValidationError(DraftingError):
    pass


class DraftingSensitiveContentError(DraftingError):
    pass


class DraftingNotFoundError(DraftingError):
    pass


class DraftingTransitionError(DraftingError):
    pass


class DraftingService:
    """Create and revise bounded local drafts without Workspace dependencies."""

    def __init__(self, database: MemoryDatabase, *, generator: _Generator | None = None) -> None:
        self.database = database
        self.repository = DraftingRepository(database)
        self._generator = generator

    def initialize(self) -> None:
        with self.database.connection() as connection:
            apply_schema(connection)

    def prepare_gmail_reply(self, source_message_id: str, instructions: str, actor: str) -> PreparedWorkspaceAction:
        source_ref = self._source_ref(source_message_id, "message id")
        return self._create_text_action("gmail_reply", source_ref, None, instructions, actor)

    def prepare_gmail_new(self, instructions: str, actor: str) -> PreparedWorkspaceAction:
        return self._create_text_action("gmail_new", None, None, instructions, actor)

    def prepare_docs_memo(self, title: str, instructions: str, actor: str) -> PreparedWorkspaceAction:
        return self._create_text_action("docs_memo", None, self._text(title, "title", 200), instructions, actor)

    def prepare_sheets_change(
        self, source_file_id: str, a1_range: str, instructions: str, actor: str
    ) -> PreparedWorkspaceAction:
        source_ref = self._source_ref(source_file_id, "file id")
        safe_range = self._a1_range(a1_range)
        safe_instructions = self._text(instructions, "instructions", 4_000)
        safe_actor = self._text(actor, "actor", 200)
        drafted_text = self._drafted_text("sheets_change", safe_instructions, source_ref, None)
        payload = json.dumps({"a1_range": safe_range, "values": [[drafted_text]]}, separators=(",", ":"))
        return self._create_action("sheets_change", source_ref, None, None, payload, safe_actor, event="prepared")

    def prepare_slides_outline(self, title: str, instructions: str, actor: str) -> PreparedWorkspaceAction:
        safe_title = self._text(title, "title", 200)
        safe_instructions = self._text(instructions, "instructions", 4_000)
        safe_actor = self._text(actor, "actor", 200)
        drafted_text = self._drafted_text("slides_outline", safe_instructions, None, safe_title)
        payload = json.dumps([{"slide_number": 1, "text": drafted_text}], separators=(",", ":"))
        return self._create_action("slides_outline", None, safe_title, None, payload, safe_actor, event="prepared")

    def revise(self, action_id: int, instructions: str, actor: str) -> PreparedWorkspaceAction:
        current = self.get_action(action_id)
        safe_instructions = self._text(instructions, "instructions", 4_000)
        safe_actor = self._text(actor, "actor", 200)
        if current.status != "prepared":
            raise DraftingTransitionError("Only prepared drafts can be revised.")
        body_text, body_payload = self._revision_content(current, safe_instructions)
        replacement = self._new_action(
            current.content_type,
            current.source_ref,
            current.title,
            body_text,
            body_payload,
            safe_actor,
            supersedes_id=current.id,
        )
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self.repository.transition_in(
                connection,
                current.id,
                "prepared",
                "superseded",
                safe_actor,
                event="superseded",
                detail="revised",
            ):
                raise DraftingTransitionError("Draft state transition is not allowed.")
            return self.repository.insert_action_in(
                connection, replacement, event="prepared", detail=f"supersedes:{current.id}"
            )

    def mark_ready_for_action(self, action_id: int, actor: str) -> PreparedWorkspaceAction:
        action = self.get_action(action_id)
        safe_actor = self._text(actor, "actor", 200)
        if action.status != "prepared":
            raise DraftingTransitionError("Draft state transition is not allowed.")
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self.repository.transition_in(
                connection, action.id, "prepared", "ready_for_action", safe_actor, event="ready_for_action"
            ):
                raise DraftingTransitionError("Draft state transition is not allowed.")
            ready = self.repository.get_action_in(connection, action.id)
            assert ready is not None
            return ready

    def get_ready_action(self, action_id: int) -> PreparedWorkspaceAction | None:
        action = self._action_or_none(action_id)
        return action if action is not None and action.status == "ready_for_action" else None

    def get_action(self, action_id: int) -> PreparedWorkspaceAction:
        action = self._action_or_none(action_id)
        if action is None:
            raise DraftingNotFoundError("Draft was not found.")
        return action

    def list_actions(self, *, limit: int = 20) -> list[PreparedWorkspaceAction]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise DraftingValidationError("Draft limit is invalid.")
        return self.repository.list_actions(limit=limit)

    def _create_text_action(
        self, content_type: str, source_ref: str | None, title: str | None, instructions: str, actor: str
    ) -> PreparedWorkspaceAction:
        safe_instructions = self._text(instructions, "instructions", 4_000)
        safe_actor = self._text(actor, "actor", 200)
        body_text = self._drafted_text(content_type, safe_instructions, source_ref, title)
        return self._create_action(content_type, source_ref, title, body_text, None, safe_actor, event="prepared")

    def _create_action(
        self,
        content_type: str,
        source_ref: str | None,
        title: str | None,
        body_text: str | None,
        body_payload: str | None,
        actor: str,
        *,
        event: str,
    ) -> PreparedWorkspaceAction:
        action = self._new_action(content_type, source_ref, title, body_text, body_payload, actor)
        self._validate_action(action)
        with self.database.connection() as connection:
            return self.repository.insert_action_in(connection, action, event=event)

    def _revision_content(self, action: PreparedWorkspaceAction, instructions: str) -> tuple[str | None, str | None]:
        drafted_text = self._drafted_text(action.content_type, instructions, action.source_ref, action.title)
        if action.content_type in _TEXT_TYPES:
            return drafted_text, None
        if action.content_type == "sheets_change":
            payload = self._payload(action)
            return None, json.dumps(
                {"a1_range": payload["a1_range"], "values": [[drafted_text]]}, separators=(",", ":")
            )
        return None, json.dumps([{"slide_number": 1, "text": drafted_text}], separators=(",", ":"))

    def _drafted_text(self, content_type: str, instructions: str, source_ref: str | None, title: str | None) -> str:
        generated = self._generator_text(content_type, instructions, source_ref, title)
        facts = f"Source reference: {source_ref}." if source_ref else "No source record was supplied."
        heading = f"Title: {title}\n" if title else ""
        return (
            f"DRAFT — LOCAL ONLY — {content_type.replace('_', ' ').upper()}\n"
            f"{heading}FACT — {facts}\n"
            "INFERENCE — The requested wording is based only on supplied instructions; intent, decisions, deadlines, and commitments are unverified.\n"
            "RECOMMENDATION — Review and edit before any external action.\n\n"
            f"DRAFTED TEXT\n{generated}"
        )

    def _generator_text(self, content_type: str, instructions: str, source_ref: str | None, title: str | None) -> str:
        if self._generator is None:
            return instructions
        try:
            generated = self._generator(content_type, instructions, source_ref, title)
            return self._text(generated, "generated draft", 4_000)
        except Exception:
            return instructions

    def _new_action(
        self,
        content_type: str,
        source_ref: str | None,
        title: str | None,
        body_text: str | None,
        body_payload: str | None,
        actor: str,
        *,
        supersedes_id: int | None = None,
    ) -> PreparedWorkspaceAction:
        now = utc_now()
        return PreparedWorkspaceAction(
            0, content_type, source_ref, title, body_text, body_payload, "prepared", supersedes_id, actor, now, now
        )

    def _validate_action(self, action: PreparedWorkspaceAction) -> None:
        if action.content_type not in PREPARED_WORKSPACE_ACTION_TYPES:
            raise DraftingValidationError("Draft content type is invalid.")
        if (action.body_text is None) == (action.body_payload is None):
            raise DraftingValidationError("Draft content is invalid.")
        if action.content_type in _TEXT_TYPES and action.body_text is None:
            raise DraftingValidationError("Draft text is required.")
        if action.content_type in _PAYLOAD_TYPES and action.body_payload is None:
            raise DraftingValidationError("Draft payload is required.")
        if action.body_text is not None:
            self._text(action.body_text, "body text", 8_000)
        if action.body_payload is not None:
            self._text(action.body_payload, "body payload", 8_000)

    def _action_or_none(self, action_id: int) -> PreparedWorkspaceAction | None:
        if not isinstance(action_id, int) or isinstance(action_id, bool) or action_id < 1:
            raise DraftingValidationError("Draft id is invalid.")
        return self.repository.get_action(action_id)

    @staticmethod
    def _payload(action: PreparedWorkspaceAction) -> dict[str, object]:
        try:
            payload = json.loads(action.body_payload or "")
        except json.JSONDecodeError as error:
            raise DraftingValidationError("Draft payload is invalid.") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("a1_range"), str):
            raise DraftingValidationError("Draft payload is invalid.")
        return payload

    @staticmethod
    def _text(value: object, field: str, limit: int, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str):
            raise DraftingValidationError(f"{field.capitalize()} is invalid.")
        normalized = value.strip()
        if (not normalized and not allow_empty) or len(normalized) > limit:
            raise DraftingValidationError(f"{field.capitalize()} is invalid.")
        if SENSITIVE_CONTENT_PATTERN.search(normalized):
            raise DraftingSensitiveContentError(f"{field.capitalize()} contains sensitive content.")
        return normalized

    @classmethod
    def _source_ref(cls, value: object, field: str) -> str:
        source_ref = cls._text(value, field, 200)
        if "://" in source_ref:
            raise DraftingValidationError(f"{field.capitalize()} is invalid.")
        return source_ref

    @staticmethod
    def _a1_range(value: object) -> str:
        if not isinstance(value, str) or not _A1_RANGE.fullmatch(value):
            raise DraftingValidationError("A1 range is invalid.")
        return value
