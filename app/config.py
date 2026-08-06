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

    # Google Workspace Settings (Optional at startup)
    google_client_secrets_path: Path | None = None
    google_token_storage_path: Path | None = None
    google_oauth_port: int = 0


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
        google_client_secrets_path=Path(google_secrets) if google_secrets else None,
        google_token_storage_path=Path(google_token) if google_token else None,
        google_oauth_port=google_port,
    )
