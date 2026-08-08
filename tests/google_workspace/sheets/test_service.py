"""Tests for bounded Google Sheets reads."""

from __future__ import annotations

import pytest

from app.google_workspace.sheets.exceptions import SheetsInvalidRequestError, SheetsNotFoundError
from app.google_workspace.sheets.service import SheetsService


class FakeFactory:
    def get_service(self, name: str, version: str) -> object:
        assert (name, version) == ("sheets", "v4")
        values = type("Values", (), {"get": lambda self, **kwargs: type("Request", (), {"execute": lambda self: {"values": [["a", "b"], [1, 2]]}})()})()
        sheets = type("Sheets", (), {
            "get": lambda self, **kwargs: type("Request", (), {"execute": lambda self: {"properties": {"title": "Plan"}, "sheets": [{"properties": {"title": "Q1"}}]}})(),
            "values": lambda self: values,
        })()
        return type("Service", (), {"spreadsheets": lambda self: sheets})()


def test_bounded_range_and_metadata_reads() -> None:
    service = SheetsService(FakeFactory())
    assert service.get_metadata("sheet_1").sheet_titles == ("Q1",)
    assert service.get_range("sheet_1", "Q1!A1:B2").rows == (("a", "b"), ("1", "2"))


@pytest.mark.parametrize("a1_range", ["A:A", "A0:B2", "Sheet1!A1:B9999999", "A1;DROP"])
def test_unbounded_or_malformed_range_is_rejected_before_provider(a1_range: str) -> None:
    with pytest.raises(SheetsInvalidRequestError):
        SheetsService(FakeFactory()).get_range("sheet_1", a1_range)


@pytest.mark.parametrize("file_id", ["", "../unsafe", "x" * 201])
def test_get_metadata_rejects_unsafe_identifiers(file_id: str) -> None:
    with pytest.raises(SheetsInvalidRequestError):
        SheetsService(FakeFactory()).get_metadata(file_id)


def test_get_range_bounds_rows_and_columns_and_cell_length() -> None:
    class WideFactory:
        def get_service(self, name: str, version: str) -> object:
            values = type("Values", (), {"get": lambda self, **kwargs: type("Request", (), {
                "execute": lambda self: {"values": [["x" * 5_000] * 60] * 250}
            })()})()
            sheets = type("Sheets", (), {"values": lambda self: values})()
            return type("Service", (), {"spreadsheets": lambda self: sheets})()

    result = SheetsService(WideFactory()).get_range("sheet_1", "A1:BH250")
    assert len(result.rows) == 200
    assert len(result.rows[0]) == 50
    assert len(result.rows[0][0]) == 1_000


def test_provider_not_found_is_normalized() -> None:
    class MissingFactory:
        def get_service(self, name: str, version: str) -> object:
            error = RuntimeError("raw provider detail")
            error.resp = type("Response", (), {"status": 404})()
            sheets = type("Sheets", (), {"get": lambda self, **kwargs: type("Request", (), {
                "execute": lambda self: (_ for _ in ()).throw(error)
            })()})()
            return type("Service", (), {"spreadsheets": lambda self: sheets})()

    with pytest.raises(SheetsNotFoundError) as error:
        SheetsService(MissingFactory()).get_metadata("sheet_1")
    assert "raw provider detail" not in str(error.value)
