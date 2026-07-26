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

    try:
        allowed_user_id = int(allowed_user_id_raw)
    except ValueError as error:
        raise ConfigurationError(
            "TELEGRAM_ALLOWED_USER_ID must be a numeric Telegram user ID"
        ) from error

    if allowed_user_id <= 0:
        raise ConfigurationError("TELEGRAM_ALLOWED_USER_ID must be a positive integer")

    return Settings(
        telegram_bot_token=token,
        telegram_allowed_user_id=allowed_user_id,
        nova_env=nova_env,
    )
