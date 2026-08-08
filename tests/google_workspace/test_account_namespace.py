"""Tests for account-derived, opaque Workspace namespaces."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from app.google_workspace import auth
from app.google_workspace.auth import GoogleAuthenticator, OAuthDependencies
from app.google_workspace.scopes import ScopeBundle


class FakeCredentials:
    def __init__(self, data: dict[str, object]) -> None:
        self.client_id = data["client_id"]
        self.scopes = data["scopes"]
        self.valid = True
        self.expired = False
        self.refresh_token = "refresh"

    @classmethod
    def from_authorized_user_info(cls, data: dict[str, object], scopes: list[str]) -> "FakeCredentials":
        return cls(data)


class Storage:
    def __init__(self, token: str | None) -> None: self.token = token
    def read_token(self) -> str | None: return self.token
    def write_token(self, token_data: str) -> None: self.token = token_data
    def delete_token(self) -> None: self.token = None


def make_authenticator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    account_subject: str | None,
    responses: list[tuple[int, str | None]] | None = None,
) -> GoogleAuthenticator:
    tmp_path.mkdir()
    secrets = tmp_path / "client.json"
    secrets.write_text(json.dumps({"installed": {"client_id": "configured-client"}}))
    data = {"client_id": "configured-client", "scopes": list(ScopeBundle.DEFAULT.value)}
    token = json.dumps(data)
    monkeypatch.setattr(auth, "_load_oauth_dependencies", lambda: OAuthDependencies(object, FakeCredentials, object, Exception, Exception))

    calls: list[str] = []

    class Response:
        def __init__(self, status_code: int, subject: str | None) -> None:
            self.status_code = status_code
            self.subject = subject

        def json(self) -> dict[str, str]:
            return {} if self.subject is None else {"sub": self.subject}

    class Session:
        def __init__(self, credentials: object) -> None: self.credentials = credentials
        def get(self, url: str, timeout: int) -> Response:
            calls.append(url)
            if responses:
                status_code, subject = responses.pop(0)
                return Response(status_code, subject)
            return Response(200, account_subject)
    google = types.ModuleType("google"); google.__path__ = []  # type: ignore[attr-defined]
    google_auth = types.ModuleType("google.auth"); google_auth.__path__ = []  # type: ignore[attr-defined]
    transport = types.ModuleType("google.auth.transport"); transport.__path__ = []  # type: ignore[attr-defined]
    requests = types.ModuleType("google.auth.transport.requests"); requests.AuthorizedSession = Session  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.auth", google_auth)
    monkeypatch.setitem(sys.modules, "google.auth.transport", transport)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", requests)
    authenticator = GoogleAuthenticator(secrets, Storage(token))
    authenticator._userinfo_call_log = calls  # type: ignore[attr-defined]
    return authenticator


def test_account_namespace_is_stable_distinct_and_not_client_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = make_authenticator(monkeypatch, tmp_path / "first", "google-subject-one")
    first_namespace = first.get_account_namespace()
    assert first_namespace == first.get_account_namespace()
    assert len(first._userinfo_call_log) == 1  # type: ignore[attr-defined]
    second = make_authenticator(monkeypatch, tmp_path / "second", "google-subject-two")
    assert first_namespace != second.get_account_namespace()
    assert first_namespace != first.get_connection_status()["client_id_hash"]
    assert "google-subject-one" not in first_namespace


def test_account_namespace_fails_closed_when_unconnected_or_identity_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    unconnected = make_authenticator(monkeypatch, tmp_path / "none", "subject")
    unconnected._storage.delete_token()  # type: ignore[attr-defined]
    assert unconnected.get_account_namespace() is None
    assert make_authenticator(monkeypatch, tmp_path / "missing", None).get_account_namespace() is None


def test_account_namespace_does_not_cache_transient_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    authenticator = make_authenticator(
        monkeypatch,
        tmp_path / "retry",
        "account-after-retry",
        responses=[(500, None), (200, "account-after-retry")],
    )

    assert authenticator.get_account_namespace() is None
    namespace = authenticator.get_account_namespace()

    assert namespace is not None
    assert authenticator.get_account_namespace() == namespace
    assert len(authenticator._userinfo_call_log) == 2  # type: ignore[attr-defined]
