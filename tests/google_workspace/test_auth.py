"""Unit tests for the Google Workspace OAuth foundation; no network calls."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.google_workspace import auth
from app.google_workspace.auth import (
    AuthError,
    GoogleAuthenticator,
    GoogleConfigurationError,
    GoogleDependencyError,
    OAuthDependencies,
    SecureFileTokenStorage,
)
from app.google_workspace.scopes import GoogleScope, ScopeBundle, canonicalize_scopes, get_scope_bundle


class FakeRefreshError(Exception):
    """Test stand-in for the real Google refresh error."""


class FakeTransportError(Exception):
    """Test stand-in for the real Google transport error."""


class FakeCredentials:
    def __init__(
        self,
        data: dict[str, object],
        *,
        valid: bool = True,
        expired: bool = False,
        refresh_error: Exception | None = None,
        refresh_client_id: str | None = None,
    ) -> None:
        self.data = dict(data)
        self.client_id = data.get("client_id")
        self.scopes = data.get("scopes")
        self.valid = valid
        self.expired = expired
        self.refresh_token = data.get("refresh_token")
        self._refresh_error = refresh_error
        self._refresh_client_id = refresh_client_id

    @classmethod
    def from_authorized_user_info(
        cls, data: dict[str, object], scopes: list[str]
    ) -> "FakeCredentials":
        return cls(data, valid=not data.get("expired", False), expired=bool(data.get("expired", False)))

    def refresh(self, request: object) -> None:
        if self._refresh_error is not None:
            raise self._refresh_error
        if self._refresh_client_id is not None:
            self.client_id = self._refresh_client_id
            self.data["client_id"] = self._refresh_client_id
        self.valid = True
        self.expired = False

    def to_json(self) -> str:
        self.data.update(
            client_id=self.client_id,
            scopes=self.scopes,
            refresh_token=self.refresh_token,
            expired=self.expired,
        )
        return json.dumps(self.data)


class InMemoryTokenStorage:
    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.write_calls = 0

    def read_token(self) -> str | None:
        return self.token

    def write_token(self, token_data: str) -> None:
        self.write_calls += 1
        self.token = token_data

    def delete_token(self) -> None:
        self.token = None


@pytest.fixture
def secrets_path(tmp_path: Path) -> Path:
    path = tmp_path / "google-client.json"
    path.write_text(json.dumps({"installed": {"client_id": "expected-client-id"}}))
    return path


@pytest.fixture
def requested_scopes() -> tuple[str, ...]:
    return ScopeBundle.DEFAULT.value


@pytest.fixture
def credential_data(requested_scopes: tuple[str, ...]) -> dict[str, object]:
    return {
        "client_id": "expected-client-id",
        "scopes": list(requested_scopes),
        "refresh_token": "test-refresh-token",
    }


def dependencies(flow_class: type[object] = MagicMock) -> OAuthDependencies:
    return OAuthDependencies(
        request_class=object,
        credentials_class=FakeCredentials,
        flow_class=flow_class,
        refresh_error=FakeRefreshError,
        transport_error=FakeTransportError,
    )


def make_authenticator(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
    storage: InMemoryTokenStorage,
    *,
    scopes: list[str] | tuple[str, ...] | None = None,
    oauth_port: int = 0,
    flow_class: type[object] = MagicMock,
) -> GoogleAuthenticator:
    monkeypatch.setattr(auth, "_load_oauth_dependencies", lambda: dependencies(flow_class))
    return GoogleAuthenticator(secrets_path, storage, scopes=scopes, oauth_port=oauth_port)


def test_dependency_loader_fails_clearly_when_google_auth_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = __import__

    def missing_google_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("google"):
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", missing_google_import)
    with pytest.raises(GoogleDependencyError, match="Google OAuth dependencies are unavailable"):
        auth._load_oauth_dependencies()


def test_scope_registry_rejects_empty_and_unknown_and_normalizes() -> None:
    email = GoogleScope.USERINFO_EMAIL.value
    profile = GoogleScope.USERINFO_PROFILE.value
    assert canonicalize_scopes([profile, email, email]) == (email, profile)
    assert ScopeBundle.DEFAULT.value == (email, profile)
    assert get_scope_bundle("DEFAULT") == (email, profile)
    with pytest.raises(ValueError, match="cannot be empty"):
        canonicalize_scopes([])
    with pytest.raises(ValueError, match="Unknown or unapproved"):
        canonicalize_scopes(["https://www.googleapis.com/auth/drive"])


def test_authenticator_rejects_invalid_ports(
    monkeypatch: pytest.MonkeyPatch, secrets_path: Path
) -> None:
    storage = InMemoryTokenStorage()
    monkeypatch.setattr(auth, "_load_oauth_dependencies", lambda: dependencies())
    for port in (-1, 65536, "8080", True):
        with pytest.raises(GoogleConfigurationError):
            GoogleAuthenticator(secrets_path, storage, oauth_port=port)  # type: ignore[arg-type]


def test_authenticator_canonicalizes_duplicate_scope_order(
    monkeypatch: pytest.MonkeyPatch, secrets_path: Path
) -> None:
    storage = InMemoryTokenStorage()
    result = make_authenticator(
        monkeypatch,
        secrets_path,
        storage,
        scopes=[GoogleScope.USERINFO_PROFILE.value, GoogleScope.USERINFO_EMAIL.value, GoogleScope.USERINFO_EMAIL.value],
    )
    assert result.get_connection_status()["scopes"] == ScopeBundle.DEFAULT.value


def test_valid_loaded_identity_and_scopes_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
    credential_data: dict[str, object],
) -> None:
    storage = InMemoryTokenStorage(json.dumps(credential_data))
    authenticator = make_authenticator(monkeypatch, secrets_path, storage)
    assert authenticator.get_credentials() is not None


def test_loaded_mismatched_identity_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
    credential_data: dict[str, object],
) -> None:
    credential_data["client_id"] = "unexpected-client-id"
    authenticator = make_authenticator(monkeypatch, secrets_path, InMemoryTokenStorage(json.dumps(credential_data)))
    with pytest.raises(AuthError, match="identity"):
        authenticator.get_credentials()


def test_missing_or_malformed_expected_client_id_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = InMemoryTokenStorage()
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json")
    authenticator = make_authenticator(monkeypatch, malformed, storage)
    with pytest.raises(GoogleConfigurationError, match="malformed"):
        authenticator.reconnect()

    missing_client_id = tmp_path / "missing-client-id.json"
    missing_client_id.write_text(json.dumps({"installed": {}}))
    authenticator = make_authenticator(monkeypatch, missing_client_id, storage)
    with pytest.raises(GoogleConfigurationError, match="missing installed.client_id"):
        authenticator.reconnect()


def test_exact_scope_comparison_rejects_partial_excess_and_missing_metadata(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
    credential_data: dict[str, object],
) -> None:
    authenticator = make_authenticator(monkeypatch, secrets_path, InMemoryTokenStorage())
    for scopes in ([GoogleScope.USERINFO_EMAIL.value], [*ScopeBundle.DEFAULT.value, GoogleScope.CALENDAR_READONLY.value], None):
        candidate = dict(credential_data)
        if scopes is None:
            candidate.pop("scopes")
        else:
            candidate["scopes"] = scopes
        with pytest.raises(AuthError):
            authenticator._validate_credentials(FakeCredentials(candidate))


@pytest.mark.parametrize("oauth_port", [0, 8765])
def test_reconnect_binds_localhost_and_configured_port_and_persists_only_on_success(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
    credential_data: dict[str, object],
    oauth_port: int,
) -> None:
    storage = InMemoryTokenStorage('{"old": "credential"}')
    credentials = FakeCredentials(credential_data)
    run_server = MagicMock(return_value=credentials)
    flow = MagicMock()
    flow.run_local_server = run_server
    flow_class = MagicMock(from_client_secrets_file=MagicMock(return_value=flow))
    authenticator = make_authenticator(monkeypatch, secrets_path, storage, oauth_port=oauth_port, flow_class=flow_class)

    authenticator.reconnect()

    run_server.assert_called_once_with(host="localhost", port=oauth_port)
    assert storage.token != '{"old": "credential"}'
    assert authenticator.get_audit_trail()[-1].success is True


def test_reconnect_rejects_identity_and_scope_changes_without_replacing_old_token(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
    credential_data: dict[str, object],
) -> None:
    storage = InMemoryTokenStorage('{"old": "credential"}')
    bad_identity = dict(credential_data, client_id="other-client")
    flow = MagicMock()
    flow.run_local_server.return_value = FakeCredentials(bad_identity)
    flow_class = MagicMock(from_client_secrets_file=MagicMock(return_value=flow))
    authenticator = make_authenticator(monkeypatch, secrets_path, storage, flow_class=flow_class)

    with pytest.raises(AuthError, match="identity"):
        authenticator.reconnect()
    assert storage.token == '{"old": "credential"}'
    assert authenticator.get_audit_trail()[-1].error_category == "identity_mismatch"


def test_refresh_revalidates_identity_and_preserves_prior_token_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
    credential_data: dict[str, object],
) -> None:
    token_data = dict(credential_data, expired=True)
    old_token = json.dumps(token_data)
    storage = InMemoryTokenStorage(old_token)

    class RefreshMutatesIdentity(FakeCredentials):
        @classmethod
        def from_authorized_user_info(cls, data: dict[str, object], scopes: list[str]) -> "RefreshMutatesIdentity":
            return cls(data, valid=False, expired=True, refresh_client_id="other-client")

    custom_dependencies = OAuthDependencies(object, RefreshMutatesIdentity, MagicMock, FakeRefreshError, FakeTransportError)
    monkeypatch.setattr(auth, "_load_oauth_dependencies", lambda: custom_dependencies)
    authenticator = GoogleAuthenticator(secrets_path, storage)

    with pytest.raises(AuthError, match="refresh failed"):
        authenticator.get_credentials()
    assert storage.token == old_token
    assert storage.write_calls == 0
    assert authenticator.get_audit_trail()[-1].error_category == "oauth_failed"



def test_refresh_exception_preserves_prior_token(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
    credential_data: dict[str, object],
) -> None:
    old_token = json.dumps(dict(credential_data, expired=True))
    storage = InMemoryTokenStorage(old_token)

    class RefreshFails(FakeCredentials):
        @classmethod
        def from_authorized_user_info(cls, data: dict[str, object], scopes: list[str]) -> "RefreshFails":
            return cls(data, valid=False, expired=True, refresh_error=FakeRefreshError("provider refresh token=secret"))

    monkeypatch.setattr(
        auth,
        "_load_oauth_dependencies",
        lambda: OAuthDependencies(object, RefreshFails, MagicMock, FakeRefreshError, FakeTransportError),
    )
    authenticator = GoogleAuthenticator(secrets_path, storage)

    with pytest.raises(AuthError, match="refresh failed"):
        authenticator.get_credentials()
    assert storage.token == old_token
    assert storage.write_calls == 0
    record = authenticator.get_audit_trail()[-1]
    assert record.error_category == "refresh_failed"
    assert "provider refresh token=secret" not in repr(record)


def test_none_oauth_result_is_malformed_and_preserves_old_token(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
) -> None:
    storage = InMemoryTokenStorage('{"old": "credential"}')
    flow = MagicMock()
    flow.run_local_server.return_value = None
    flow_class = MagicMock(from_client_secrets_file=MagicMock(return_value=flow))
    authenticator = make_authenticator(monkeypatch, secrets_path, storage, flow_class=flow_class)

    with pytest.raises(AuthError, match="no credentials"):
        authenticator.reconnect()
    assert storage.token == '{"old": "credential"}'
    assert authenticator.get_audit_trail()[-1].error_category == "malformed_response"

def test_expired_token_without_refresh_token_requires_reconnect(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
    credential_data: dict[str, object],
) -> None:
    token_data = dict(credential_data, expired=True, refresh_token=None)
    authenticator = make_authenticator(monkeypatch, secrets_path, InMemoryTokenStorage(json.dumps(token_data)))
    with pytest.raises(AuthError, match="reconnect required"):
        authenticator.get_credentials()


def test_oauth_failure_categories_never_store_raw_provider_text(
    monkeypatch: pytest.MonkeyPatch,
    secrets_path: Path,
) -> None:
    storage = InMemoryTokenStorage('{"old": "credential"}')
    scenarios = [
        ("access_denied provider says secret=abc", "consent_denied"),
        ("request timeout provider says token=abc", "timeout"),
        ("browser cancellation auth-code=abc", "flow_aborted"),
        ("malformed response state=abc", "malformed_response"),
    ]
    for message, category in scenarios:
        flow = MagicMock()
        flow.run_local_server.side_effect = RuntimeError(message)
        flow_class = MagicMock(from_client_secrets_file=MagicMock(return_value=flow))
        authenticator = make_authenticator(monkeypatch, secrets_path, storage, flow_class=flow_class)
        with pytest.raises(AuthError, match="OAuth reconnect failed"):
            authenticator.reconnect()
        record = authenticator.get_audit_trail()[-1]
        assert record.error_category == category
        assert message not in repr(record)
        assert "secret=abc" not in repr(record)


def test_audit_records_and_returned_trail_are_immutable_and_non_sensitive(
    monkeypatch: pytest.MonkeyPatch, secrets_path: Path
) -> None:
    authenticator = make_authenticator(monkeypatch, secrets_path, InMemoryTokenStorage())
    authenticator.local_disconnect()
    trail = authenticator.get_audit_trail()
    assert isinstance(trail, tuple)
    assert isinstance(trail[0].scopes, tuple)
    assert "expected-client-id" not in repr(trail[0])
    with pytest.raises(AttributeError):
        trail.append(trail[0])  # type: ignore[attr-defined]
    assert len(authenticator.get_audit_trail()) == 1


def test_local_disconnect_is_idempotent(monkeypatch: pytest.MonkeyPatch, secrets_path: Path) -> None:
    storage = InMemoryTokenStorage('{"token": "local-only"}')
    authenticator = make_authenticator(monkeypatch, secrets_path, storage)
    authenticator.local_disconnect()
    authenticator.local_disconnect()
    assert storage.token is None
    assert len(authenticator.get_audit_trail()) == 2


def test_storage_corrects_permissions_and_rejects_malformed_and_symlinks(tmp_path: Path) -> None:
    token_path = tmp_path / "private" / "token.json"
    storage = SecureFileTokenStorage(token_path)
    storage.write_token('{"token": "one"}')
    assert stat.S_IMODE(token_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600

    token_path.chmod(0o644)
    assert storage.read_token() == '{"token": "one"}'
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600

    token_path.write_text("not-json")
    with pytest.raises(AuthError, match="malformed"):
        storage.read_token()

    target = tmp_path / "real-token.json"
    target.write_text('{"token": "one"}')
    symlink_path = tmp_path / "link-token.json"
    symlink_path.symlink_to(target)
    with pytest.raises(GoogleConfigurationError, match="Symlinked token paths"):
        SecureFileTokenStorage(symlink_path).read_token()

    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(GoogleConfigurationError, match="parent directories"):
        SecureFileTokenStorage(linked_parent / "token.json").write_token('{"token": "one"}')


def test_atomic_write_failures_preserve_old_token_and_clean_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_path = tmp_path / "token.json"
    storage = SecureFileTokenStorage(token_path)
    storage.write_token('{"token": "old"}')

    for target, failure in (("fsync", OSError("fsync failed")), ("replace", OSError("replace failed"))):
        if target == "fsync":
            monkeypatch.setattr(os, "fsync", lambda descriptor: (_ for _ in ()).throw(failure))
        else:
            monkeypatch.setattr(os, "fsync", lambda descriptor: None)
            monkeypatch.setattr(os, "replace", lambda source, destination: (_ for _ in ()).throw(failure))

        with pytest.raises(OSError):
            storage.write_token('{"token": "new"}')
        assert token_path.read_text() == '{"token": "old"}'
        assert list(tmp_path.glob(".nova-google-token-*")) == []
