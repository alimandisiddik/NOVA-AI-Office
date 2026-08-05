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
    nova_provider_default_model: str = ""
    nova_provider_allowed_models: list[str] = None


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

    allowed_list = [m.strip() for m in provider_allowed.split(",") if m.strip()] if provider_allowed else []

    return Settings(
        telegram_bot_token=token,
        telegram_allowed_user_id=allowed_user_id,
        nova_env=nova_env,
        nova_memory_db_path=(Path(memory_path) if Path(memory_path).is_absolute() else _repository_env_file().parent / memory_path),
        nova_provider_base_url=provider_base,
        nova_provider_api_key=provider_key,
        nova_provider_default_model=provider_default,
        nova_provider_allowed_models=allowed_list if allowed_list else ([],) [0],
    )
