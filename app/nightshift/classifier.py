"""Explicit hard allowlist for future night-shift job integrations."""

from __future__ import annotations

from app.nightshift.models import NightJobClassification

APPROVED_OVERNIGHT = "approved_overnight"
DEFERRED_UNTIL_MORNING = "deferred_until_morning"
CRITICAL_NOTIFY_ONLY = "critical_notify_only"
PROHIBITED = "prohibited"

REGISTERED_JOBS: dict[str, NightJobClassification] = {
    "execution_status_check": NightJobClassification("execution_status_check", "read_only", APPROVED_OVERNIGHT),
    "draft_summary_prepare": NightJobClassification("draft_summary_prepare", "draft_only", APPROVED_OVERNIGHT),
    "dissertation_review_prepare": NightJobClassification("dissertation_review_prepare", "draft_only", DEFERRED_UNTIL_MORNING),
    # Sprint 5G: provider-backed overnight jobs. 9Router-combo continuity is
    # structural here — the Coding/Architecture provider chains only place a
    # direct specialist first when one is configured, so these run
    # unattended without ever depending on local Codex/Claude availability.
    "coding_task_prepare": NightJobClassification("coding_task_prepare", "draft_only", APPROVED_OVERNIGHT),
    "architecture_review_prepare": NightJobClassification("architecture_review_prepare", "draft_only", APPROVED_OVERNIGHT),
    "generic_ai_task_prepare": NightJobClassification("generic_ai_task_prepare", "draft_only", APPROVED_OVERNIGHT),
    "essential_service_repeated_failure": NightJobClassification("essential_service_repeated_failure", "read_only", CRITICAL_NOTIFY_ONLY),
    "git_commit": NightJobClassification("git_commit", "git_mutation", PROHIBITED),
    "git_push": NightJobClassification("git_push", "git_mutation", PROHIBITED),
    "email_send": NightJobClassification("email_send", "external_communication", PROHIBITED),
    "whatsapp_send": NightJobClassification("whatsapp_send", "external_communication", PROHIBITED),
    "calendar_mutation": NightJobClassification("calendar_mutation", "calendar_mutation", PROHIBITED),
    "file_delete": NightJobClassification("file_delete", "destructive", PROHIBITED),
    "file_move": NightJobClassification("file_move", "destructive", PROHIBITED),
    "document_overwrite": NightJobClassification("document_overwrite", "document_overwrite", PROHIBITED),
    "secret_change": NightJobClassification("secret_change", "secret_change", PROHIBITED),
    "purchase": NightJobClassification("purchase", "paid_action", PROHIBITED),
    "destructive_migration": NightJobClassification("destructive_migration", "destructive_migration", PROHIBITED),
    "git_merge": NightJobClassification("git_merge", "git_mutation", PROHIBITED),
    "git_reset": NightJobClassification("git_reset", "git_mutation", PROHIBITED),
    "git_rebase": NightJobClassification("git_rebase", "git_mutation", PROHIBITED),
    "telegram_outbound_message": NightJobClassification("telegram_outbound_message", "external_communication", PROHIBITED),
}


def classify_job_type(job_type: str) -> NightJobClassification | None:
    """Return the registered classification or ``None`` for unknown types."""
    return REGISTERED_JOBS.get(job_type)
