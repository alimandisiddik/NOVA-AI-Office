"""Tests for bounded Google Docs structural reads."""

from __future__ import annotations

import pytest

from app.google_workspace.docs.exceptions import DocsInvalidRequestError, DocsNotFoundError
from app.google_workspace.docs.service import DocsService


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
