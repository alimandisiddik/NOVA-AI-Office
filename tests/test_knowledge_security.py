"""Security and persistence-boundary tests for Knowledge Operations."""

from pathlib import Path

import pytest

from app.knowledge.service import InvalidKnowledgeValueError, KnowledgeService
from app.memory.database import MemoryDatabase


@pytest.fixture()
def service(tmp_path: Path) -> KnowledgeService:
    database = MemoryDatabase(tmp_path / "knowledge.sqlite3")
    database.initialize()
    result = KnowledgeService(database)
    result.initialize()
    return result


@pytest.mark.parametrize("sensitive", ["api_key=secret", "password=secret", "Bearer abc", "BEGIN PRIVATE KEY"])
def test_sensitive_content_is_rejected_before_any_write(service: KnowledgeService, sensitive: str) -> None:
    attempts = [
        lambda: service.create_source(sensitive, "manual", "Citation", "owner"),
        lambda: service.create_source("Title", "manual", sensitive, "owner"),
        lambda: service.create_source("Title", "manual", "Citation", "owner", origin_ref=sensitive),
        lambda: service.create_source("Title", "manual", "Citation", sensitive),
    ]
    source = service.create_source("Safe title", "manual", "Safe citation", "owner")
    attempts.extend([
        lambda: service.create_item(source.id, sensitive, "Summary", "owner"),
        lambda: service.create_item(source.id, "Title", sensitive, "owner"),
        lambda: service.create_item(source.id, "Title", "Summary", "owner", tags=sensitive),
    ])

    for attempt in attempts:
        with pytest.raises(InvalidKnowledgeValueError, match="Sensitive values cannot be stored"):
            attempt()

    assert service.query() == []


def test_origin_reference_rejects_url_credentials(service: KnowledgeService) -> None:
    with pytest.raises(InvalidKnowledgeValueError, match="Origin reference cannot contain URL credentials"):
        service.create_source(
            "Title", "drive_file", "Citation", "owner", origin_ref="https://user:pass@example.test/document"
        )

    assert service.query() == []


@pytest.mark.parametrize(
    "origin_ref",
    [
        "user:hunter2@example.test/doc",
        "admin:s3cr3tPass@drive.google.com/file",
    ],
)
def test_origin_reference_rejects_userinfo_credentials_without_scheme(
    service: KnowledgeService, origin_ref: str
) -> None:
    with pytest.raises(InvalidKnowledgeValueError, match="Origin reference cannot contain URL credentials"):
        service.create_source("Title", "drive_file", "Citation", "owner", origin_ref=origin_ref)

    assert service.query() == []


@pytest.mark.parametrize(
    "origin_ref",
    [
        "https://drive.google.com/file?token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "https://drive.google.com/file?access_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "https://drive.google.com/file?sig=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
    ],
)
def test_origin_reference_rejects_credential_bearing_query_parameters(
    service: KnowledgeService, origin_ref: str
) -> None:
    with pytest.raises(
        InvalidKnowledgeValueError, match="Origin reference cannot contain credential-bearing query parameters"
    ):
        service.create_source("Title", "drive_file", "Citation", "owner", origin_ref=origin_ref)

    assert service.query() == []


def test_origin_reference_accepts_opaque_references(service: KnowledgeService) -> None:
    source = service.create_source(
        "Title", "drive_file", "Citation", "owner", origin_ref="https://drive.google.com/file/d/abc123/view"
    )

    assert source.origin_ref == "https://drive.google.com/file/d/abc123/view"


def test_free_text_length_bounds_are_enforced(service: KnowledgeService) -> None:
    with pytest.raises(InvalidKnowledgeValueError, match="Title exceeds maximum length of 500"):
        service.create_source("x" * 501, "manual", "Citation", "owner")
    source = service.create_source("Title", "manual", "Citation", "owner")
    with pytest.raises(InvalidKnowledgeValueError, match="Summary exceeds maximum length of 1000"):
        service.create_item(source.id, "Finding", "x" * 1001, "owner")
