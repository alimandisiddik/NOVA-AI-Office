"""Tests for the Google discovery client factory; no real service calls."""

from __future__ import annotations

import builtins
from unittest.mock import ANY, MagicMock

import unittest
from unittest.mock import MagicMock, patch

from app.google_workspace.auth import AuthError, GoogleDependencyError
from app.google_workspace import factory
from app.google_workspace.factory import GoogleClientFactory


class ConnectedAuthenticator:
    def get_credentials(self, require_valid: bool = True) -> object:
        return object()


class DisconnectedAuthenticator:
    def get_credentials(self, require_valid: bool = True) -> None:
        return None


class TestFactory(unittest.TestCase):
    @patch('builtins.__import__')
    def test_missing_discovery_dependency_fails_clearly(self, mock_import) -> None:
        real_import = __import__
        def missing_discovery_import(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("googleapiclient"):
                raise ImportError("simulated missing dependency")
            return real_import(name, *args, **kwargs)
        mock_import.side_effect = missing_discovery_import
        with self.assertRaisesRegex(GoogleDependencyError, "Google API discovery dependency is unavailable"):
            factory._load_discovery_builder()


    def test_factory_rejects_unapproved_service_and_disconnected_state(self) -> None:
        disconnected = GoogleClientFactory(DisconnectedAuthenticator())  # type: ignore[arg-type]
        with self.assertRaisesRegex(GoogleDependencyError, "not approved"):
            disconnected.get_service("gmail", "v1")
        with self.assertRaisesRegex(AuthError, "reconnect required"):
            disconnected.get_service("calendar", "v3")


    @patch('app.google_workspace.factory._load_discovery_builder')
    def test_factory_uses_mocked_discovery_boundary_with_cache_disabled(self, mock_load) -> None:
        build = MagicMock(return_value="mock-service")
        mock_load.return_value = build
        service = GoogleClientFactory(ConnectedAuthenticator()).get_service("calendar", "v3")  # type: ignore[arg-type]
        self.assertEqual(service, "mock-service")
        build.assert_called_once_with("calendar", "v3", credentials=ANY, cache_discovery=False)
