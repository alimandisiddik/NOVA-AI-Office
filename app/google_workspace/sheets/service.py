"""Bounded Google Sheets reads with no mutation surface."""

from __future__ import annotations

import re
from typing import Any

from app.google_workspace.factory import GoogleClientFactory
from app.google_workspace.sheets.dtos import RangeValues, SpreadsheetMetadata
from app.google_workspace.sheets.exceptions import (
    SheetsAuthenticationError, SheetsInvalidRequestError, SheetsNetworkError, SheetsNotFoundError,
    SheetsPermissionError, SheetsProviderError, SheetsRateLimitError, SheetsServiceError,
)

_A1_RANGE = re.compile(r"^(?:(?:'[^']{1,60}'|[A-Za-z_][\w ]{0,60})!)?\$?[A-Z]{1,3}\$?[1-9]\d{0,5}(?::\$?[A-Z]{1,3}\$?[1-9]\d{0,5})?$")
_MAX_ROWS = 200
_MAX_COLUMNS = 50
_MAX_CELL_LENGTH = 1_000


class SheetsService:
    """Expose metadata plus explicitly bounded A1 range reads."""

    def __init__(self, client_factory: GoogleClientFactory) -> None:
        self._client_factory = client_factory

    def get_metadata(self, file_id: str) -> SpreadsheetMetadata:
        if not self._valid_file_id(file_id):
            raise SheetsInvalidRequestError()
        try:
            raw = self._client_factory.get_service("sheets", "v4").spreadsheets().get(
                spreadsheetId=file_id, fields="spreadsheetId,properties.title,sheets.properties.title"
            ).execute()
            if not isinstance(raw, dict): raise SheetsProviderError()
            titles = tuple(
                sheet.get("properties", {}).get("title", "")[:200]
                for sheet in raw.get("sheets", []) if isinstance(sheet, dict) and isinstance(sheet.get("properties"), dict)
            )[:200]
            return SpreadsheetMetadata(file_id, str(raw.get("properties", {}).get("title", ""))[:200], titles)
        except SheetsServiceError: raise
        except Exception as error: raise self._map_error(error) from None

    def get_range(self, file_id: str, a1_range: str) -> RangeValues:
        if not self._valid_file_id(file_id) or not isinstance(a1_range, str) or not _A1_RANGE.fullmatch(a1_range):
            raise SheetsInvalidRequestError()
        try:
            raw = self._client_factory.get_service("sheets", "v4").spreadsheets().values().get(
                spreadsheetId=file_id, range=a1_range
            ).execute()
            values = raw.get("values", []) if isinstance(raw, dict) else []
            if not isinstance(values, list): raise SheetsProviderError()
            rows = tuple(
                tuple(str(cell)[:_MAX_CELL_LENGTH] for cell in row[:_MAX_COLUMNS])
                for row in values[:_MAX_ROWS] if isinstance(row, list)
            )
            return RangeValues(file_id, a1_range, rows)
        except SheetsServiceError: raise
        except Exception as error: raise self._map_error(error) from None

    @staticmethod
    def _valid_file_id(value: object) -> bool:
        return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value))

    @staticmethod
    def _map_error(error: Exception) -> SheetsServiceError:
        status = getattr(error, "status_code", None) or getattr(getattr(error, "resp", None), "status", None)
        if status == 401: return SheetsAuthenticationError()
        if status == 403: return SheetsPermissionError()
        if status == 404: return SheetsNotFoundError()
        if status == 429: return SheetsRateLimitError()
        if status == 400: return SheetsInvalidRequestError()
        if isinstance(error, (TimeoutError, ConnectionError, OSError)): return SheetsNetworkError()
        return SheetsProviderError()
