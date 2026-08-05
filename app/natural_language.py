"""Deterministic Indonesian and simple-English Workspace Memory intent parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceIntent:
    """A rule-based intent or a user-facing clarification request."""

    action: str | None = None
    fields: dict[str, str] | None = None
    clarification: str | None = None


STATUS_ALIASES = {
    "todo": "todo",
    "belum mulai": "todo",
    "doing": "doing",
    "dikerjakan": "doing",
    "done": "done",
    "selesai": "done",
    "completed": "done",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "batal": "cancelled",
    "dibatalkan": "cancelled",
}


def parse_workspace_intent(message: str) -> WorkspaceIntent | None:
    """Parse supported natural-language memory requests without external services."""
    text = _clean(message)
    if not text:
        return None

    if re.fullmatch(r"(?:buat|buatkan|create|add)\s+(?:project|proyek)", text, re.IGNORECASE):
        return _clarify("Saya perlu nama project. Contoh: Buat project NOVA AI Office")

    match = re.fullmatch(
        r"(?:buat|buatkan|create|add)\s+(?:project|proyek)\s+(.+)", text, re.IGNORECASE
    )
    if match:
        return _intent("create_project", name=_capture(match, 1))

    if re.fullmatch(
        r"(?:tampilkan|lihat|daftar|list|show)\s+(?:semua\s+)?(?:project|proyek)s?", text, re.IGNORECASE
    ):
        return _intent("list_projects")

    match = re.fullmatch(
        r"(?:tambahkan|buat|create|add)\s+(?:task|tugas)\s+(.+?)\s+"
        r"(?:di|untuk|in|for)\s+(?:project|proyek)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if match:
        return _intent("create_task", title=_capture(match, 1), project_name=_capture(match, 2))

    match = re.fullmatch(
        r"(?:tampilkan|lihat|daftar|list|show)\s+(?:task|tugas)\s+"
        r"(?:di|untuk|in|for)\s+(?:project|proyek)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if match:
        return _intent("list_tasks", project_name=_capture(match, 1))

    if re.fullmatch(r"(?:tampilkan|lihat|daftar|list|show)\s+(?:task|tugas)s?", text, re.IGNORECASE):
        return _clarify("Untuk project mana? Contoh: Tampilkan task di project NOVA AI Office")

    status_intent = _parse_task_status(text)
    if status_intent:
        return status_intent

    match = re.fullmatch(
        r"(?:catat|tambahkan|buat|add|create)\s+(?:catatan|note)\s+(.+?)\s+"
        r"(?:di|untuk|in|for)\s+(?:project|proyek)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if match:
        return _intent("create_note", content=_capture(match, 1), project_name=_capture(match, 2))

    if re.match(r"(?:catat|tambahkan|buat|add|create)\s+(?:catatan|note)\b", text, re.IGNORECASE):
        return _clarify("Untuk project mana? Contoh: Catat note ... di project NOVA AI Office")

    match = re.fullmatch(
        r"(?:catat|buat|add|create)\s+(?:keputusan|decision)(?:\s+(?:bahwa|that))?\s+(.+?)\s+"
        r"(?:di|untuk|in|for)\s+(?:project|proyek)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if match:
        return _intent("create_decision", decision=_capture(match, 1), project_name=_capture(match, 2))

    if re.match(r"(?:catat|buat|add|create)\s+(?:keputusan|decision)\b", text, re.IGNORECASE):
        return _clarify("Untuk project mana? Contoh: Catat keputusan ... di project NOVA AI Office")

    match = re.fullmatch(
        r"(?:catat|rekam|buat|record|log)\s+(?:sesi|session)\s+(.+?)\s+"
        r"(?:di|untuk|in|for)\s+(?:project|proyek)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if match:
        return _intent("create_session", summary=_capture(match, 1), project_name=_capture(match, 2))

    if re.match(r"(?:catat|rekam|buat|record|log)\s+(?:sesi|session)\b", text, re.IGNORECASE):
        return _clarify("Untuk project mana? Contoh: Rekam sesi testing di project NOVA AI Office")

    match = re.fullmatch(r"(?:apa\s+)?(?:progress|kemajuan)\s+(?:project|proyek)\s+(.+)", text, re.IGNORECASE)
    if match:
        return _intent("show_progress", project_name=_capture(match, 1))

    match = re.fullmatch(r"(?:show|what is)\s+(?:the\s+)?progress\s+(?:for\s+)?(?:project\s+)?(.+)", text, re.IGNORECASE)
    if match:
        return _intent("show_progress", project_name=_capture(match, 1))

    match = re.fullmatch(r"(?:resume)\s+(?:project|proyek)\s+(.+)", text, re.IGNORECASE)
    if match:
        return _intent("resume_project", project_name=_capture(match, 1))

    match = re.fullmatch(r"(?:lanjutkan|continue)\s+(?:project|proyek)\s+(.+)", text, re.IGNORECASE)
    if match:
        return _intent("continue_project", project_name=_capture(match, 1))

    return None


def _parse_task_status(text: str) -> WorkspaceIntent | None:
    status_pattern = "|".join(re.escape(alias) for alias in sorted(STATUS_ALIASES, key=len, reverse=True))
    match = re.fullmatch(
        rf"(?:tandai|ubah|set|update)\s+(?:task|tugas)\s+(.+?)\s+"
        rf"(?:menjadi|ke|as|to)?\s*({status_pattern})"
        rf"(?:\s+(?:di|untuk|in|for)\s+(?:project|proyek)\s+(.+))?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    task_identifier = _capture(match, 1)
    status = STATUS_ALIASES[_capture(match, 2).lower()]
    project_name = _capture(match, 3) if match.group(3) else ""
    if not project_name:
        return _clarify(
            "Untuk project mana? Contoh: Tandai task Workspace Memory selesai di project NOVA AI Office"
        )
    return _intent(
        "update_task_status",
        task_identifier=task_identifier,
        status=status,
        project_name=project_name,
    )


def _intent(action: str, **fields: str) -> WorkspaceIntent:
    return WorkspaceIntent(action=action, fields=fields)


def _clarify(message: str) -> WorkspaceIntent:
    return WorkspaceIntent(clarification=message)


def _capture(match: re.Match[str], index: int) -> str:
    return _clean(match.group(index))


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().rstrip("?.!")
