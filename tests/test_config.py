import pytest
from pathlib import Path

from app.config import ConfigurationError, load_settings


def valid_environment() -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USER_ID": "123456789",
        "NOVA_ENV": "test",
    }


def test_missing_bot_token_is_rejected() -> None:
    environment = valid_environment()
    del environment["TELEGRAM_BOT_TOKEN"]

    with pytest.raises(ConfigurationError, match="TELEGRAM_BOT_TOKEN"):
        load_settings(environment)


def test_missing_allowed_user_id_is_rejected() -> None:
    environment = valid_environment()
    del environment["TELEGRAM_ALLOWED_USER_ID"]

    with pytest.raises(ConfigurationError, match="TELEGRAM_ALLOWED_USER_ID"):
        load_settings(environment)


def test_invalid_allowed_user_id_is_rejected() -> None:
    environment = valid_environment()
    environment["TELEGRAM_ALLOWED_USER_ID"] = "not-a-number"

    with pytest.raises(ConfigurationError, match="numeric Telegram user ID"):
        load_settings(environment)


def test_valid_configuration_is_loaded() -> None:
    settings = load_settings(valid_environment())

    assert settings.telegram_bot_token == "test-token"
    assert settings.telegram_allowed_user_id == 123456789
    assert settings.nova_env == "test"
    assert settings.nova_memory_db_path == Path(__file__).resolve().parent.parent / "data/nova_memory.db"


def test_configurable_memory_database_path_is_loaded() -> None:
    environment = valid_environment()
    environment["NOVA_MEMORY_DB_PATH"] = "runtime/workspace.sqlite3"

    settings = load_settings(environment)

    assert settings.nova_memory_db_path == Path(__file__).resolve().parent.parent / "runtime/workspace.sqlite3"
