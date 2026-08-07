"""Sensitive-content and bounded-storage tests for Sprint 6A."""
from pathlib import Path
import pytest
from app.dissertation.service import DissertationService
from app.dissertation.repository import DissertationTargetNotFoundError, InvalidDissertationValueError
from app.memory.database import MemoryDatabase

SENSITIVE_PATTERNS = [
    "api_key=secret",
    "password=secret",
    "token=secret",
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDsecret",
    "-----BEGIN RSA PRIVATE KEY-----",
    '{"telegram_bot_token": "secret"}',
]


@pytest.fixture
def service(tmp_path: Path) -> DissertationService:
    service = DissertationService(MemoryDatabase(tmp_path / "nova.sqlite3"))
    service.initialize()
    service.initialize_workspace("Thesis", "PhD", "owner")
    service.create_chapter("Ch1", 1)
    return service


def _cases():
    """One case per (field, pattern) pair, covering every new Sprint 6A
    free-text field the spec names: current_focus, citation_text, locator,
    gap description, note content, evidence summary."""
    for pattern in SENSITIVE_PATTERNS:
        yield lambda s, p=pattern: s.set_current_focus(p, "owner")
        yield lambda s, p=pattern: s.create_source("Source", "book", p, "owner")
        yield lambda s, p=pattern: s.create_source("Source", "book", "Citation", "owner", locator=p)
        yield lambda s, p=pattern: s.create_gap(p, "other", "owner")
        yield lambda s, p=pattern: s.create_note("other", p, "owner", chapter_id=1)
        yield lambda s, p=pattern: s.create_evidence(
            s.create_source("Source", "book", "Citation", "owner").id, p, "owner"
        )


@pytest.mark.parametrize("factory", list(_cases()))
def test_sensitive_content_is_rejected(service: DissertationService, factory) -> None:
    with pytest.raises(InvalidDissertationValueError):
        factory(service)


def test_evidence_summary_is_bounded_and_source_backed(service: DissertationService) -> None:
    source = service.create_source("Source", "book", "Citation", "owner")
    with pytest.raises(InvalidDissertationValueError):
        service.create_evidence(source.id, "x" * 1001, "owner")
    with pytest.raises(DissertationTargetNotFoundError):
        service.create_evidence(999, "Supported claim", "owner")
