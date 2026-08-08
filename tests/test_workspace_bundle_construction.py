"""Exercises app/main.py's own real WorkspaceConnectorBundle construction path.

The G4 review found the previous state hardcoded ``google_workspace = None``
in app/main.py — every existing integration test constructed a bundle by
hand and passed it into build_application(), never exercising main.py's own
construction logic. These tests call app.main's real, non-test construction
functions directly (the exact functions app.main.main() calls) to prove the
production path actually builds a working bundle, fails closed when
unconfigured, and never touches the network.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from app.config import Settings
from app.google_workspace.bundle import WorkspaceConnectorBundle
from app.google_workspace.calendar.service import CalendarService
from app.google_workspace.docs.service import DocsService
from app.google_workspace.drive.service import DriveReadService
from app.google_workspace.gmail.service import GmailService
from app.google_workspace.sheets.service import SheetsService
from app.google_workspace.slides.service import SlidesService
from app.main import _drive_read_service_for_settings, _workspace_bundle_for_settings


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def network_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Workspace bundle construction must not make a network call")

    monkeypatch.setattr(socket, "create_connection", network_forbidden)


def _client_secrets(tmp_path: Path) -> Path:
    path = tmp_path / "google-client.json"
    path.write_text('{"installed": {"client_id": "test-client"}}', encoding="utf-8")
    return path


def _base_settings(tmp_path: Path, **overrides: object) -> Settings:
    fields = dict(
        telegram_bot_token="test-token",
        telegram_allowed_user_id=7,
        nova_env="test",
        nova_memory_db_path=tmp_path / "nova.sqlite3",
    )
    fields.update(overrides)
    return Settings(**fields)  # type: ignore[arg-type]


def test_unconfigured_settings_yield_no_bundle(tmp_path: Path) -> None:
    settings = _base_settings(tmp_path)

    assert _workspace_bundle_for_settings(settings) is None


def test_configured_without_drive_allowlist_builds_partial_bundle(tmp_path: Path) -> None:
    settings = _base_settings(
        tmp_path,
        google_client_secrets_path=_client_secrets(tmp_path),
        google_token_storage_path=tmp_path / "google-token.json",
    )

    bundle = _workspace_bundle_for_settings(settings)

    assert isinstance(bundle, WorkspaceConnectorBundle)
    assert bundle.drive is None
    assert isinstance(bundle.gmail, GmailService)
    assert isinstance(bundle.calendar, CalendarService)
    assert isinstance(bundle.docs, DocsService)
    assert isinstance(bundle.sheets, SheetsService)
    assert isinstance(bundle.slides, SlidesService)
    assert bundle.authenticator.get_connection_status()["is_connected"] is False


def test_configured_with_valid_drive_allowlist_builds_full_bundle(tmp_path: Path) -> None:
    settings = _base_settings(
        tmp_path,
        google_client_secrets_path=_client_secrets(tmp_path),
        google_token_storage_path=tmp_path / "google-token.json",
        google_drive_allowed_folders=(("folder-123", "workspace"),),
    )

    bundle = _workspace_bundle_for_settings(settings)

    assert isinstance(bundle, WorkspaceConnectorBundle)
    assert isinstance(bundle.drive, DriveReadService)
    assert bundle.drive.list_allowed_folders()[0].alias == "workspace"
    # Every other service is unaffected by Drive being configured.
    assert isinstance(bundle.gmail, GmailService)
    assert isinstance(bundle.calendar, CalendarService)


def test_invalid_drive_allowlist_degrades_to_drive_unavailable_without_raising(tmp_path: Path) -> None:
    settings = _base_settings(
        tmp_path,
        google_client_secrets_path=_client_secrets(tmp_path),
        google_token_storage_path=tmp_path / "google-token.json",
        # Same alias twice: DriveReadService itself rejects this.
        google_drive_allowed_folders=(("folder-1", "dup"), ("folder-2", "dup")),
    )

    bundle = _workspace_bundle_for_settings(settings)

    assert isinstance(bundle, WorkspaceConnectorBundle)
    assert bundle.drive is None
    assert isinstance(bundle.gmail, GmailService)
    assert isinstance(bundle.calendar, CalendarService)
    assert isinstance(bundle.docs, DocsService)


def test_drive_read_service_helper_returns_none_when_unconfigured(tmp_path: Path) -> None:
    settings = _base_settings(
        tmp_path,
        google_client_secrets_path=_client_secrets(tmp_path),
        google_token_storage_path=tmp_path / "google-token.json",
    )
    bundle = _workspace_bundle_for_settings(settings)
    assert bundle is not None

    assert _drive_read_service_for_settings(settings, bundle.factory) is None


def test_main_end_to_end_wires_configured_bundle_into_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs the real app.main.main() production path (settings -> bundle ->
    build_application), the exact path that was previously broken by a
    hardcoded google_workspace=None. Only I/O boundaries that are not the
    subject of this test (logging setup, the Telegram Application object,
    and run_polling) are stubbed; construction of every Wave 7 service,
    including WorkspaceConnectorBundle, is real.
    """
    import app.main as main_module

    captured: dict[str, object] = {}

    class _FakeApplication:
        def run_polling(self) -> None:
            captured["ran_polling"] = True

    def fake_build_application(*args: object, **kwargs: object) -> _FakeApplication:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeApplication()

    settings = _base_settings(
        tmp_path,
        google_client_secrets_path=_client_secrets(tmp_path),
        google_token_storage_path=tmp_path / "google-token.json",
        google_drive_allowed_folders=(("folder-123", "workspace"),),
    )

    monkeypatch.setattr(main_module, "configure_logging", lambda: None)
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "build_application", fake_build_application)

    exit_code = main_module.main()

    assert exit_code == 0
    assert captured["ran_polling"] is True
    kwargs = captured["kwargs"]
    bundle = kwargs["google_workspace"]
    assert isinstance(bundle, WorkspaceConnectorBundle)
    assert isinstance(bundle.drive, DriveReadService)

    workspace_intel = kwargs["workspace_intel"]
    assert workspace_intel is not None
    assert workspace_intel._gmail is bundle.gmail
    assert workspace_intel._calendar is bundle.calendar

    workspace_bridge = kwargs["workspace_bridge"]
    assert workspace_bridge.authenticator is bundle.authenticator


def test_main_end_to_end_unconfigured_stays_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.main as main_module

    captured: dict[str, object] = {}

    class _FakeApplication:
        def run_polling(self) -> None:
            captured["ran_polling"] = True

    def fake_build_application(*args: object, **kwargs: object) -> _FakeApplication:
        captured["kwargs"] = kwargs
        return _FakeApplication()

    settings = _base_settings(tmp_path)

    monkeypatch.setattr(main_module, "configure_logging", lambda: None)
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "build_application", fake_build_application)

    exit_code = main_module.main()

    assert exit_code == 0
    kwargs = captured["kwargs"]
    assert kwargs["google_workspace"] is None
    assert kwargs["workspace_intel"] is None
    assert kwargs["workspace_bridge"].authenticator.get_account_namespace() is None


def test_configured_bundle_never_calls_get_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.google_workspace.auth import GoogleAuthenticator

    def credentials_forbidden(self: GoogleAuthenticator, *args: object, **kwargs: object) -> None:
        raise AssertionError("Bundle construction must not load/refresh credentials")

    monkeypatch.setattr(GoogleAuthenticator, "get_credentials", credentials_forbidden)
    monkeypatch.setattr(GoogleAuthenticator, "reconnect", credentials_forbidden)

    settings = _base_settings(
        tmp_path,
        google_client_secrets_path=_client_secrets(tmp_path),
        google_token_storage_path=tmp_path / "google-token.json",
        google_drive_allowed_folders=(("folder-123", "workspace"),),
    )

    bundle = _workspace_bundle_for_settings(settings)

    assert bundle is not None
