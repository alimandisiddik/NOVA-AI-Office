"""Google Workspace configuration validation remains optional for startup."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import ConfigurationError, load_settings


def base_environment() -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USER_ID": "12345",
        "NOVA_ENV": "test",
    }


def test_google_configuration_is_optional() -> None:
    settings = load_settings(base_environment())
    assert settings.google_client_secrets_path is None
    assert settings.google_token_storage_path is None
    assert settings.google_oauth_port == 0


def test_google_configuration_loads_valid_paths_and_port(tmp_path: Path) -> None:
    environment = base_environment()
    environment.update(
        GOOGLE_CLIENT_SECRETS_PATH=str(tmp_path / "client.json"),
        GOOGLE_TOKEN_STORAGE_PATH=str(tmp_path / "token.json"),
        GOOGLE_OAUTH_PORT="8123",
    )
    settings = load_settings(environment)
    assert settings.google_client_secrets_path == tmp_path / "client.json"
    assert settings.google_token_storage_path == tmp_path / "token.json"
    assert settings.google_oauth_port == 8123


@pytest.mark.parametrize("value", ["-1", "65536", "not-a-port"])
def test_google_port_is_validated(value: str) -> None:
    environment = base_environment()
    environment["GOOGLE_OAUTH_PORT"] = value
    with pytest.raises(ConfigurationError, match="GOOGLE_OAUTH_PORT"):
        load_settings(environment)


def test_google_paths_must_be_configured_together(tmp_path: Path) -> None:
    environment = base_environment()
    environment["GOOGLE_CLIENT_SECRETS_PATH"] = str(tmp_path / "client.json")
    with pytest.raises(ConfigurationError, match="configured together"):
        load_settings(environment)
