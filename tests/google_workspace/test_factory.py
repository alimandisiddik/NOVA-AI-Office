"""Tests for the Google discovery client factory; no real service calls."""

from __future__ import annotations

import builtins
from unittest.mock import ANY, MagicMock

import pytest

from app.google_workspace.auth import AuthError, GoogleDependencyError
from app.google_workspace import factory
from app.google_workspace.factory import GoogleClientFactory


class ConnectedAuthenticator:
    def get_credentials(self, require_valid: bool = True) -> object:
        return object()


class DisconnectedAuthenticator:
    def get_credentials(self, require_valid: bool = True) -> None:
        return None


def test_missing_discovery_dependency_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def missing_discovery_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("googleapiclient"):
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_discovery_import)
    with pytest.raises(GoogleDependencyError, match="Google API discovery dependency is unavailable"):
        factory._load_discovery_builder()


def test_factory_rejects_unapproved_service_and_disconnected_state() -> None:
    disconnected = GoogleClientFactory(DisconnectedAuthenticator())  # type: ignore[arg-type]
    with pytest.raises(GoogleDependencyError, match="not approved"):
        disconnected.get_service("drive", "v3")
    with pytest.raises(AuthError, match="reconnect required"):
        disconnected.get_service("calendar", "v3")


def test_factory_uses_mocked_discovery_boundary_with_cache_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    build = MagicMock(return_value="mock-service")
    monkeypatch.setattr(factory, "_load_discovery_builder", lambda: build)
    service = GoogleClientFactory(ConnectedAuthenticator()).get_service("calendar", "v3")  # type: ignore[arg-type]
    assert service == "mock-service"
    build.assert_called_once_with("calendar", "v3", credentials=ANY, cache_discovery=False)
