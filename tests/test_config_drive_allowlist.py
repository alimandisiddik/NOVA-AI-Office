"""GOOGLE_DRIVE_ALLOWED_FOLDERS parsing — the G4 Drive-allowlist configuration fix."""

from __future__ import annotations

import pytest

from app.config import ConfigurationError, load_settings


def valid_environment() -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "TELEGRAM_ALLOWED_USER_ID": "123456789",
        "NOVA_ENV": "test",
    }


def test_drive_allowlist_defaults_to_empty() -> None:
    settings = load_settings(valid_environment())

    assert settings.google_drive_allowed_folders == ()


def test_drive_allowlist_parses_single_pair() -> None:
    environment = valid_environment()
    environment["GOOGLE_DRIVE_ALLOWED_FOLDERS"] = "folder-123:workspace"

    settings = load_settings(environment)

    assert settings.google_drive_allowed_folders == (("folder-123", "workspace"),)


def test_drive_allowlist_parses_multiple_pairs() -> None:
    environment = valid_environment()
    environment["GOOGLE_DRIVE_ALLOWED_FOLDERS"] = "folder-1:alpha, folder-2:beta"

    settings = load_settings(environment)

    assert settings.google_drive_allowed_folders == (("folder-1", "alpha"), ("folder-2", "beta"))


def test_drive_allowlist_missing_colon_is_rejected() -> None:
    environment = valid_environment()
    environment["GOOGLE_DRIVE_ALLOWED_FOLDERS"] = "folder-123-workspace"

    with pytest.raises(ConfigurationError, match="folder_id:alias"):
        load_settings(environment)


def test_drive_allowlist_empty_folder_id_is_rejected() -> None:
    environment = valid_environment()
    environment["GOOGLE_DRIVE_ALLOWED_FOLDERS"] = ":workspace"

    with pytest.raises(ConfigurationError, match="non-empty folder id and alias"):
        load_settings(environment)


def test_drive_allowlist_empty_alias_is_rejected() -> None:
    environment = valid_environment()
    environment["GOOGLE_DRIVE_ALLOWED_FOLDERS"] = "folder-123:"

    with pytest.raises(ConfigurationError, match="non-empty folder id and alias"):
        load_settings(environment)


def test_drive_allowlist_duplicate_alias_is_rejected() -> None:
    environment = valid_environment()
    environment["GOOGLE_DRIVE_ALLOWED_FOLDERS"] = "folder-1:workspace,folder-2:workspace"

    with pytest.raises(ConfigurationError, match="aliases must be unique"):
        load_settings(environment)


def test_drive_allowlist_blank_value_stays_empty() -> None:
    environment = valid_environment()
    environment["GOOGLE_DRIVE_ALLOWED_FOLDERS"] = "   "

    settings = load_settings(environment)

    assert settings.google_drive_allowed_folders == ()
