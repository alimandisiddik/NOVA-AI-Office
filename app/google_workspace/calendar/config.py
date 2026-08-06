"""Calendar-owned configuration used during later Control Tower wiring."""

from __future__ import annotations

from dataclasses import dataclass

from app.google_workspace.scopes import ScopeBundle, get_scope_bundle

DEFAULT_TIMEZONE = "Asia/Jakarta"
DEFAULT_SEARCH_WINDOW_DAYS = 7
MAX_SEARCH_WINDOW_DAYS = 31
DEFAULT_GLOBAL_RESULT_LIMIT = 100
MAX_GLOBAL_RESULT_LIMIT = 250
MAX_CALENDAR_IDENTIFIERS = 10
CALENDAR_SCOPES = get_scope_bundle(ScopeBundle.CALENDAR)


@dataclass(frozen=True)
class CalendarIntegrationConfig:
    """Static Calendar integration policy; values do not read environment state."""

    calendar_aliases: tuple[tuple[str, str], ...] = (("primary", "primary"),)
    default_timezone: str = DEFAULT_TIMEZONE
    default_search_window_days: int = DEFAULT_SEARCH_WINDOW_DAYS
    max_search_window_days: int = MAX_SEARCH_WINDOW_DAYS
    default_global_result_limit: int = DEFAULT_GLOBAL_RESULT_LIMIT
    max_global_result_limit: int = MAX_GLOBAL_RESULT_LIMIT
    max_calendar_identifiers: int = MAX_CALENDAR_IDENTIFIERS

    def aliases(self) -> dict[str, str]:
        return dict(self.calendar_aliases)
