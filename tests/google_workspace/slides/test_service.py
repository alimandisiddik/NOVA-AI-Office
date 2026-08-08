"""Tests for bounded Google Slides structural reads."""

from __future__ import annotations

import pytest

from app.google_workspace.slides.exceptions import SlidesInvalidRequestError, SlidesNotFoundError
from app.google_workspace.slides.service import SlidesService


def _presentation_service(raw: object) -> object:
    return type("Service", (), {"presentations": lambda self: type(
        "Presentations", (), {"get": lambda self, **kwargs: type("Request", (), {"execute": lambda self: raw})()}
    )()})()


class FakeFactory:
    def get_service(self, name: str, version: str) -> object:
        assert (name, version) == ("slides", "v1")
        raw = {"title": "Review", "slides": [{"pageElements": [{"shape": {"text": {"textElements": [
            {"textRun": {"content": "Agenda\n"}}, {"textRun": {"content": "Risks"}},
        ]}}}]}]}
        return _presentation_service(raw)


def test_get_presentation_extracts_slide_text_only() -> None:
    presentation = SlidesService(FakeFactory()).get_presentation("slide_1")
    assert presentation.title == "Review"
    assert presentation.slides[0].text_fragments == ("Agenda", "Risks")
    assert presentation.truncated is False


@pytest.mark.parametrize("file_id", ["", "../unsafe", "x" * 201])
def test_get_presentation_rejects_unsafe_identifiers(file_id: str) -> None:
    with pytest.raises(SlidesInvalidRequestError):
        SlidesService(FakeFactory()).get_presentation(file_id)


def test_get_presentation_truncates_beyond_slide_bound() -> None:
    class ManySlidesFactory:
        def get_service(self, name: str, version: str) -> object:
            raw = {"title": "Big deck", "slides": [{"pageElements": []} for _ in range(150)]}
            return _presentation_service(raw)

    presentation = SlidesService(ManySlidesFactory()).get_presentation("slide_1")
    assert len(presentation.slides) == 100
    assert presentation.truncated is True


def test_get_presentation_truncates_fragments_and_fragment_length() -> None:
    class ManyFragmentsFactory:
        def get_service(self, name: str, version: str) -> object:
            text_elements = [{"textRun": {"content": f"fragment {i}"}} for i in range(150)]
            text_elements.append({"textRun": {"content": "y" * 5_000}})
            raw = {"title": "Dense", "slides": [{"pageElements": [
                {"shape": {"text": {"textElements": text_elements}}}
            ]}]}
            return _presentation_service(raw)

    presentation = SlidesService(ManyFragmentsFactory()).get_presentation("slide_1")
    assert len(presentation.slides[0].text_fragments) == 100
    assert presentation.truncated is True


def test_provider_not_found_is_normalized() -> None:
    class MissingFactory:
        def get_service(self, name: str, version: str) -> object:
            error = RuntimeError("raw provider detail")
            error.resp = type("Response", (), {"status": 404})()
            return type("Service", (), {"presentations": lambda self: type(
                "Presentations", (), {"get": lambda self, **kwargs: type("Request", (), {"execute": lambda self: (_ for _ in ()).throw(error)})()}
            )()})()

    with pytest.raises(SlidesNotFoundError) as error:
        SlidesService(MissingFactory()).get_presentation("slide_1")
    assert "raw provider detail" not in str(error.value)
