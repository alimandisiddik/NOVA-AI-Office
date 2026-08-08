"""Configuration loading and validation for NOVA AI Office."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Raised when required local configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Validated settings needed by the local Telegram bot."""

    telegram_bot_token: str
    telegram_allowed_user_id: int
    nova_env: str
    nova_memory_db_path: Path
    nova_provider_base_url: str = ""
    nova_provider_api_key: str = ""
    nova_provider_model_priority: list[str] = None
    nova_provider_default_model: str = ""
    nova_provider_allowed_models: list[str] = None

    nova_codex_executable_path: str = ""
    nova_claude_executable_path: str = ""
    nova_provider_coding_combo_priority: list[str] = None
    nova_provider_review_combo_priority: list[str] = None
    # NOVA internal alias -> 9Router upstream/combo route id overrides
    # (Sprint 5G.1). Registry defaults in app/providers/registry.py are
    # authoritative unless overridden here.
    nova_provider_upstream_route_map: dict[str, str] = None

    # Google Workspace Settings (Optional at startup)
    google_client_secrets_path: Path | None = None
    google_token_storage_path: Path | None = None
    google_oauth_port: int = 0
    # Bounded Drive folder allowlist for read-only Workspace operations.
    # Empty by default; Drive read capability stays unavailable until this
    # is explicitly configured. Does not gate Gmail/Calendar/Docs/Sheets/
    # Slides read availability.
    google_drive_allowed_folders: tuple[tuple[str, str], ...] = ()

    # Executive Dashboard Settings (default off; used only by app.dashboard.server)
    nova_dashboard_enabled: bool = False
    nova_dashboard_port: int = 8787


def _repository_env_file() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


def _required_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required environment variable is missing: {name}")
    return value


def load_settings(environment: Mapping[str, str] | None = None) -> Settings:
    """Load local settings without exposing secret values in error messages."""
    if environment is None:
        load_dotenv(dotenv_path=_repository_env_file(), override=False)
        environment = os.environ

    token = _required_value(environment, "TELEGRAM_BOT_TOKEN")
    allowed_user_id_raw = _required_value(environment, "TELEGRAM_ALLOWED_USER_ID")
    nova_env = _required_value(environment, "NOVA_ENV")
    memory_path = environment.get("NOVA_MEMORY_DB_PATH", "data/nova_memory.db").strip()
    if not memory_path:
        raise ConfigurationError("NOVA_MEMORY_DB_PATH must not be empty")

    try:
        allowed_user_id = int(allowed_user_id_raw)
    except ValueError as error:
        raise ConfigurationError(
            "TELEGRAM_ALLOWED_USER_ID must be a numeric Telegram user ID"
        ) from error

    if allowed_user_id <= 0:
        raise ConfigurationError("TELEGRAM_ALLOWED_USER_ID must be a positive integer")

    # Provider config is optional at the global settings level so tests/components
    # without provider can run, but ProviderGatewayService validates it heavily.
    provider_base = environment.get("NOVA_PROVIDER_BASE_URL", "").strip()
    provider_key = environment.get("NOVA_PROVIDER_API_KEY", "").strip()
    provider_default = environment.get("NOVA_PROVIDER_DEFAULT_MODEL", "").strip()
    provider_allowed = environment.get("NOVA_PROVIDER_ALLOWED_MODELS", "").strip()
    provider_priority = environment.get("NOVA_PROVIDER_MODEL_PRIORITY", "").strip()

    allowed_list = [m.strip() for m in provider_allowed.split(",") if m.strip()] if provider_allowed else []
    priority_list = [m.strip() for m in provider_priority.split(",") if m.strip()] if provider_priority else []

    codex_path = environment.get("NOVA_CODEX_EXECUTABLE_PATH", "").strip()
    claude_path = environment.get("NOVA_CLAUDE_EXECUTABLE_PATH", "").strip()
    coding_priority = environment.get("NOVA_PROVIDER_CODING_COMBO_PRIORITY", "").strip()
    review_priority = environment.get("NOVA_PROVIDER_REVIEW_COMBO_PRIORITY", "").strip()

    upstream_route_map_raw = environment.get("NOVA_PROVIDER_UPSTREAM_ROUTE_MAP", "").strip()
    upstream_route_map: dict[str, str] = {}
    if upstream_route_map_raw:
        for entry in upstream_route_map_raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                raise ConfigurationError(
                    "NOVA_PROVIDER_UPSTREAM_ROUTE_MAP entries must be 'alias:route' pairs"
                )
            alias, _, route = entry.partition(":")
            alias, route = alias.strip(), route.strip()
            if not alias or not route:
                raise ConfigurationError(
                    "NOVA_PROVIDER_UPSTREAM_ROUTE_MAP entries must have a non-empty alias and route"
                )
            upstream_route_map[alias] = route

    # Google Workspace Config
    google_secrets = environment.get("GOOGLE_CLIENT_SECRETS_PATH", "").strip()
    google_token = environment.get("GOOGLE_TOKEN_STORAGE_PATH", "").strip()
    google_port_raw = environment.get("GOOGLE_OAUTH_PORT", "0").strip()

    try:
        google_port = int(google_port_raw)
    except ValueError as error:
        raise ConfigurationError("GOOGLE_OAUTH_PORT must be an integer") from error
    if not 0 <= google_port <= 65535:
        raise ConfigurationError("GOOGLE_OAUTH_PORT must be between 0 and 65535")

    if bool(google_secrets) != bool(google_token):
        raise ConfigurationError(
            "GOOGLE_CLIENT_SECRETS_PATH and GOOGLE_TOKEN_STORAGE_PATH must be configured together"
        )

    # Bounded Drive folder allowlist: comma-separated "folder_id:alias" pairs.
    # Unset/empty means Drive read capability stays unavailable; other
    # Workspace read services are unaffected.
    drive_folders_raw = environment.get("GOOGLE_DRIVE_ALLOWED_FOLDERS", "").strip()
    drive_allowed_folders: list[tuple[str, str]] = []
    if drive_folders_raw:
        seen_aliases: set[str] = set()
        for entry in drive_folders_raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" not in entry:
                raise ConfigurationError(
                    "GOOGLE_DRIVE_ALLOWED_FOLDERS entries must be 'folder_id:alias' pairs"
                )
            folder_id, _, alias = entry.partition(":")
            folder_id, alias = folder_id.strip(), alias.strip()
            if not folder_id or not alias:
                raise ConfigurationError(
                    "GOOGLE_DRIVE_ALLOWED_FOLDERS entries must have a non-empty folder id and alias"
                )
            if alias in seen_aliases:
                raise ConfigurationError(
                    "GOOGLE_DRIVE_ALLOWED_FOLDERS aliases must be unique"
                )
            seen_aliases.add(alias)
            drive_allowed_folders.append((folder_id, alias))

    dashboard_enabled_raw = environment.get("NOVA_DASHBOARD_ENABLED", "false").strip().lower()
    if dashboard_enabled_raw not in {"true", "false"}:
        raise ConfigurationError("NOVA_DASHBOARD_ENABLED must be true or false")
    dashboard_port_raw = environment.get("NOVA_DASHBOARD_PORT", "8787").strip()
    try:
        dashboard_port = int(dashboard_port_raw)
    except ValueError as error:
        raise ConfigurationError("NOVA_DASHBOARD_PORT must be an integer") from error
    if not 1 <= dashboard_port <= 65535:
        raise ConfigurationError("NOVA_DASHBOARD_PORT must be between 1 and 65535")

    return Settings(
        telegram_bot_token=token,
        telegram_allowed_user_id=allowed_user_id,
        nova_env=nova_env,
        nova_memory_db_path=(Path(memory_path) if Path(memory_path).is_absolute() else _repository_env_file().parent / memory_path),
        nova_provider_base_url=provider_base,
        nova_provider_api_key=provider_key,
        nova_provider_model_priority=priority_list if priority_list else ([],) [0],
        nova_provider_default_model=provider_default,
        nova_provider_allowed_models=allowed_list if allowed_list else ([],) [0],
        nova_codex_executable_path=codex_path,
        nova_claude_executable_path=claude_path,
        nova_provider_coding_combo_priority=[item.strip() for item in coding_priority.split(",") if item.strip()],
        nova_provider_review_combo_priority=[item.strip() for item in review_priority.split(",") if item.strip()],
        nova_provider_upstream_route_map=upstream_route_map,
        google_client_secrets_path=Path(google_secrets) if google_secrets else None,
        google_token_storage_path=Path(google_token) if google_token else None,
        google_oauth_port=google_port,
        google_drive_allowed_folders=tuple(drive_allowed_folders),
        nova_dashboard_enabled=dashboard_enabled_raw == "true",
        nova_dashboard_port=dashboard_port,
    )
