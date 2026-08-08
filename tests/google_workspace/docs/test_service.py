"""Tests for bounded Google Docs structural reads."""

from __future__ import annotations

import pytest

from app.google_workspace.docs.exceptions import (
    DocsInvalidRequestError, DocsNotFoundError, DocsPermissionError, DocsSubmissionUncertainError,
)
from app.google_workspace.docs.service import DocsService, DocsWriteService


def _document_service(raw: object) -> object:
    return type("Service", (), {"documents": lambda self: type(
        "Documents", (), {"get": lambda self, **kwargs: type("Request", (), {"execute": lambda self: raw})()}
    )()})()


class FakeFactory:
    def get_service(self, name: str, version: str) -> object:
        assert (name, version) == ("docs", "v1")
        raw = {"title": "Research notes", "body": {"content": [
            {"paragraph": {"elements": [{"textRun": {"content": "First paragraph\n"}}]}},
            {"paragraph": {"elements": [{"textRun": {"content": "Second paragraph"}}]}},
        ]}}
        return _document_service(raw)


def test_get_document_extracts_plain_text_paragraphs() -> None:
    document = DocsService(FakeFactory()).get_document("document_1")
    assert document.title == "Research notes"
    assert document.paragraphs == ("First paragraph", "Second paragraph")
    assert document.truncated is False


def test_invalid_file_identifier_never_reaches_provider() -> None:
    with pytest.raises(DocsInvalidRequestError):
        DocsService(FakeFactory()).get_document("../../unsafe")


def test_get_document_truncates_beyond_bound() -> None:
    class ManyParagraphsFactory:
        def get_service(self, name: str, version: str) -> object:
            raw = {"title": "Long", "body": {"content": [
                {"paragraph": {"elements": [{"textRun": {"content": f"Paragraph {i}"}}]}}
                for i in range(250)
            ]}}
            return _document_service(raw)

    document = DocsService(ManyParagraphsFactory()).get_document("document_1")
    assert len(document.paragraphs) == 200
    assert document.truncated is True


def test_get_document_truncates_oversized_paragraph_text() -> None:
    class LongParagraphFactory:
        def get_service(self, name: str, version: str) -> object:
            raw = {"title": "Long", "body": {"content": [
                {"paragraph": {"elements": [{"textRun": {"content": "x" * 5_000}}]}},
            ]}}
            return _document_service(raw)

    document = DocsService(LongParagraphFactory()).get_document("document_1")
    assert len(document.paragraphs[0]) == 1_000
    assert document.truncated is True


def test_provider_not_found_is_normalized() -> None:
    class MissingFactory:
        def get_service(self, name: str, version: str) -> object:
            error = RuntimeError("raw provider detail")
            error.resp = type("Response", (), {"status": 404})()
            return type("Service", (), {"documents": lambda self: type(
                "Documents", (), {"get": lambda self, **kwargs: type("Request", (), {"execute": lambda self: (_ for _ in ()).throw(error)})()}
            )()})()

    with pytest.raises(DocsNotFoundError) as error:
        DocsService(MissingFactory()).get_document("document_1")
    assert "raw provider detail" not in str(error.value)


def test_write_service_creates_then_inserts_the_approved_body() -> None:
    calls = []

    class Documents:
        def create(self, *, body):
            calls.append(("create", body))
            return type("Request", (), {"execute": lambda self: {"documentId": "doc-1"}})()

        def batchUpdate(self, *, documentId, body):
            calls.append(("batchUpdate", documentId, body))
            return type("Request", (), {"execute": lambda self: {}})()

    class Client:
        def documents(self):
            return Documents()

    factory = type("Factory", (), {"get_service": lambda self, name, version: Client()})()
    result = DocsWriteService(factory).create_private_document("Memo", "Approved body")

    assert result.document_id == "doc-1"
    assert calls == [
        ("create", {"title": "Memo"}),
        ("batchUpdate", "doc-1", {"requests": [{"insertText": {"location": {"index": 1}, "text": "Approved body"}}]}),
    ]


def test_write_service_network_failure_on_create_is_outcome_unknown_not_failed() -> None:
    """A timeout/connection drop on documents.create() itself is exactly the
    ambiguous case the frozen contract calls 'outcome_unknown': the request
    may have reached Google and created an orphan document before the
    response was lost. Only a definite server rejection (permission/auth/
    invalid/rate-limit) is safe to treat as 'no write happened'."""

    class Documents:
        def create(self, *, body):
            raise ConnectionError("connection reset")

    class Client:
        def documents(self):
            return Documents()

    factory = type("Factory", (), {"get_service": lambda self, name, version: Client()})()
    with pytest.raises(DocsSubmissionUncertainError):
        DocsWriteService(factory).create_private_document("Memo", "Body")


def test_write_service_definite_rejection_on_create_is_failed_not_outcome_unknown() -> None:
    class DefiniteError(Exception):
        status_code = 403

    class Documents:
        def create(self, *, body):
            raise DefiniteError("permission denied")

    class Client:
        def documents(self):
            return Documents()

    factory = type("Factory", (), {"get_service": lambda self, name, version: Client()})()
    with pytest.raises(DocsPermissionError):
        DocsWriteService(factory).create_private_document("Memo", "Body")
