"""Fail-closed Google OAuth desktop-flow and local token lifecycle support.

The callback server always binds to ``localhost``. ``oauth_port=0`` asks the OS
for an ephemeral local port; any explicit port is passed unchanged to the OAuth
library after validation. This module never starts a flow or contacts Google
until ``reconnect()`` or a credential refresh is explicitly requested.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from app.google_workspace.scopes import ScopeBundle, canonicalize_scopes


class GoogleWorkspaceError(Exception):
    """Base error for the Google Workspace foundation."""


class GoogleDependencyError(GoogleWorkspaceError):
    """Raised when required Google client libraries are unavailable."""


class GoogleConfigurationError(GoogleWorkspaceError):
    """Raised when Google OAuth configuration is unsafe or malformed."""


class AuthError(GoogleWorkspaceError):
    """Raised for safe, user-actionable OAuth lifecycle failures."""


class OAuthDependencies(NamedTuple):
    """Google library boundary loaded only when an OAuth action needs it."""

    request_class: type[Any]
    credentials_class: type[Any]
    flow_class: type[Any]
    refresh_error: type[Exception]
    transport_error: type[Exception]


def _load_oauth_dependencies() -> OAuthDependencies:
    """Load real Google libraries or fail clearly without creating stubs."""
    try:
        from google.auth.exceptions import RefreshError, TransportError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as error:
        raise GoogleDependencyError(
            "Google OAuth dependencies are unavailable; install google-auth and "
            "google-auth-oauthlib before enabling Google Workspace."
        ) from error

    return OAuthDependencies(Request, Credentials, InstalledAppFlow, RefreshError, TransportError)


class TokenStorageProtocol(Protocol):
    """Secure local token storage boundary; implementations must never log tokens."""

    def read_token(self) -> str | None: ...

    def write_token(self, token_data: str) -> None: ...

    def delete_token(self) -> None: ...


@dataclass(frozen=True)
class AuthAuditLog:
    """Immutable, non-sensitive OAuth lifecycle metadata."""

    timestamp: datetime
    action: str
    client_id_hash: str
    scopes: tuple[str, ...]
    success: bool
    error_category: str | None = None


class SecureFileTokenStorage:
    """POSIX-oriented local token storage with atomic replacement.

    The target and direct parent are rejected when symlinked. They are checked
    again immediately before ``os.replace``. A portable implementation cannot
    entirely eliminate a race from another process changing the path between
    that check and replacement; NOVA's supported model is a single-user local
    desktop directory with restrictive ownership and permissions.
    """

    def __init__(self, token_path: Path):
        self._path = token_path

    def _reject_symlinks(self) -> None:
        if self._path.is_symlink():
            raise GoogleConfigurationError("Symlinked token paths are not allowed")
        if self._path.parent.is_symlink():
            raise GoogleConfigurationError("Symlinked token parent directories are not allowed")

    @staticmethod
    def _set_directory_permissions(path: Path) -> None:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    @staticmethod
    def _set_file_permissions(path: Path) -> None:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def read_token(self) -> str | None:
        self._reject_symlinks()
        if not self._path.exists():
            return None
        self._set_file_permissions(self._path)
        try:
            token_data = self._path.read_text(encoding="utf-8")
            json.loads(token_data)
        except json.JSONDecodeError as error:
            raise AuthError("Stored token data is malformed") from error
        return token_data

    def write_token(self, token_data: str) -> None:
        """Persist valid serialized credentials without overwriting on failure."""
        self._reject_symlinks()
        try:
            json.loads(token_data)
        except json.JSONDecodeError as error:
            raise GoogleConfigurationError("Refusing to store malformed token JSON") from error

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._reject_symlinks()
        self._set_directory_permissions(self._path.parent)

        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=".nova-google-token-",
        )
        temporary_path = Path(temporary_name)
        try:
            self._set_file_permissions(temporary_path)
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                temporary_file.write(token_data)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            self._reject_symlinks()
            os.replace(temporary_path, self._path)
            self._set_file_permissions(self._path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def delete_token(self) -> None:
        self._reject_symlinks()
        if self._path.exists():
            self._path.unlink()


class GoogleAuthenticator:
    """Manage a single configured Google OAuth desktop connection.

    Expected identity is read from ``installed.client_id`` in the configured
    client-secrets JSON. Every loaded, refreshed, and newly-authorized
    credential must expose the identical client ID and the exact requested
    scope set. No background refresh is performed.
    """

    def __init__(
        self,
        client_secrets_path: Path,
        token_storage: TokenStorageProtocol,
        scopes: list[str] | tuple[str, ...] | None = None,
        oauth_port: int = 0,
    ):
        self._secrets_path = Path(client_secrets_path)
        self._storage = token_storage
        requested_scopes = ScopeBundle.DEFAULT.value if scopes is None else scopes
        self._scopes = canonicalize_scopes(requested_scopes)
        self._oauth_port = self._validate_port(oauth_port)
        self._audit_logs: list[AuthAuditLog] = []
        self._account_namespace_cache: str | None = None

    @staticmethod
    def _validate_port(port: object) -> int:
        if isinstance(port, bool) or not isinstance(port, int):
            raise GoogleConfigurationError("OAuth port must be an integer")
        if not 0 <= port <= 65535:
            raise GoogleConfigurationError("OAuth port must be between 0 and 65535")
        return port

    def _read_expected_client_id(self) -> str:
        if not self._secrets_path.exists():
            raise GoogleConfigurationError("Google client-secrets file is missing")
        try:
            secrets_data = json.loads(self._secrets_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise GoogleConfigurationError("Google client-secrets JSON is malformed") from error

        client_id = secrets_data.get("installed", {}).get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise GoogleConfigurationError("Google client-secrets JSON is missing installed.client_id")
        return client_id

    def _client_identity_hash(self) -> str:
        try:
            client_id = self._read_expected_client_id()
        except GoogleConfigurationError:
            return "unavailable"
        return hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:12]

    def _audit(self, action: str, success: bool, error_category: str | None = None) -> None:
        self._audit_logs.append(
            AuthAuditLog(
                timestamp=datetime.now(timezone.utc),
                action=action,
                client_id_hash=self._client_identity_hash(),
                scopes=tuple(self._scopes),
                success=success,
                error_category=error_category,
            )
        )

    @staticmethod
    def _failure_category(error: Exception) -> str:
        """Map OAuth failures to stable categories without retaining text."""
        text = str(error).lower()
        if "access_denied" in text or "denied" in text:
            return "consent_denied"
        if "timeout" in text:
            return "timeout"
        if "cancel" in text or "abort" in text:
            return "flow_aborted"
        if "malformed" in text or "invalid response" in text:
            return "malformed_response"
        return "oauth_failed"

    def _validate_credentials(self, credentials: Any) -> None:
        expected_client_id = self._read_expected_client_id()
        actual_client_id = getattr(credentials, "client_id", None)
        if not isinstance(actual_client_id, str) or actual_client_id != expected_client_id:
            raise AuthError("Credential identity does not match configured OAuth client")

        granted_scopes = getattr(credentials, "scopes", None)
        if not granted_scopes:
            raise AuthError("Credential scope metadata is missing")
        try:
            canonical_granted_scopes = canonicalize_scopes(tuple(granted_scopes))
        except ValueError as error:
            raise AuthError("Credential scopes are not approved") from error
        if canonical_granted_scopes != self._scopes:
            raise AuthError("Credential scopes do not exactly match requested scopes")

    def _deserialize_credentials(self, token_data: str) -> Any:
        dependencies = _load_oauth_dependencies()
        try:
            return dependencies.credentials_class.from_authorized_user_info(
                json.loads(token_data),
                scopes=list(self._scopes),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise AuthError("Stored token data cannot be loaded") from error

    @staticmethod
    def _serialize_credentials(credentials: Any) -> str:
        try:
            serialized = credentials.to_json()
            json.loads(serialized)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AuthError("OAuth credentials cannot be serialized safely") from error
        return serialized

    def get_credentials(self, require_valid: bool = True) -> Any | None:
        """Load and, only when requested, refresh local credentials on demand."""
        token_data = self._storage.read_token()
        if token_data is None:
            return None

        credentials = self._deserialize_credentials(token_data)
        try:
            self._validate_credentials(credentials)
        except (AuthError, GoogleConfigurationError):
            if require_valid:
                raise
            return None

        if getattr(credentials, "valid", False):
            return credentials
        if not getattr(credentials, "expired", False):
            if require_valid:
                raise AuthError("Stored credentials are invalid; reconnect required")
            return None
        if not getattr(credentials, "refresh_token", None):
            if require_valid:
                raise AuthError("Stored credentials expired; reconnect required")
            return None

        dependencies = _load_oauth_dependencies()
        try:
            credentials.refresh(dependencies.request_class())
            self._validate_credentials(credentials)
            refreshed_token_data = self._serialize_credentials(credentials)
            self._storage.write_token(refreshed_token_data)
        except Exception as error:
            category = "refresh_failed" if isinstance(error, dependencies.refresh_error) else self._failure_category(error)
            self._audit("refresh", False, category)
            if require_valid:
                raise AuthError("Credential refresh failed; reconnect required") from None
            return None

        self._audit("refresh", True)
        return credentials

    def reconnect(self) -> None:
        """Run the desktop flow and replace storage only after full validation."""
        try:
            self._read_expected_client_id()
        except GoogleConfigurationError:
            self._audit("reconnect", False, "invalid_configuration")
            raise
        dependencies = _load_oauth_dependencies()
        try:
            flow = dependencies.flow_class.from_client_secrets_file(
                str(self._secrets_path),
                scopes=list(self._scopes),
            )
            credentials = flow.run_local_server(host="localhost", port=self._oauth_port)
            if credentials is None:
                raise AuthError("OAuth flow returned no credentials")
            self._validate_credentials(credentials)
            token_data = self._serialize_credentials(credentials)
            self._storage.write_token(token_data)
        except GoogleConfigurationError:
            self._audit("reconnect", False, "invalid_configuration")
            raise
        except AuthError as error:
            category = "identity_mismatch" if "identity" in str(error).lower() else "scope_mismatch"
            if "no credentials" in str(error).lower():
                category = "malformed_response"
            self._audit("reconnect", False, category)
            raise
        except Exception as error:
            self._audit("reconnect", False, self._failure_category(error))
            raise AuthError("OAuth reconnect failed") from None

        self._audit("reconnect", True)
        self._account_namespace_cache = None

    def local_disconnect(self) -> None:
        """Remove only local cached credentials; this does not revoke Google access."""
        self._storage.delete_token()
        self._account_namespace_cache = None
        self._audit("local_disconnect", True)

    def get_connection_status(self) -> dict[str, Any]:
        """Return non-sensitive status only; never token or credential contents."""
        try:
            credentials = self.get_credentials(require_valid=False)
        except (AuthError, GoogleConfigurationError, GoogleDependencyError):
            credentials = None
        return {
            "is_connected": bool(credentials and getattr(credentials, "valid", False)),
            "expired": bool(credentials and getattr(credentials, "expired", False)),
            "has_refresh_token": bool(credentials and getattr(credentials, "refresh_token", None)),
            "scopes": tuple(self._scopes),
            "client_id_hash": self._client_identity_hash(),
        }

    def get_account_namespace(self) -> str | None:
        """Return an opaque, stable namespace derived from the authenticated account.

        ``google.oauth2.credentials.Credentials`` does not expose a validated
        account identifier itself.  The supported Google-auth boundary for this
        OAuth credential is an authenticated userinfo request, whose ``sub``
        claim is a stable account identifier.  Any unavailable dependency,
        invalid credential, or provider failure fails closed as ``None``.
        """
        if self._account_namespace_cache is not None:
            return self._account_namespace_cache
        try:
            credentials = self.get_credentials(require_valid=False)
            if credentials is None or not getattr(credentials, "valid", False):
                return None
            self._validate_credentials(credentials)

            from google.auth.transport.requests import AuthorizedSession

            response = AuthorizedSession(credentials).get(
                "https://openidconnect.googleapis.com/v1/userinfo", timeout=5
            )
            if getattr(response, "status_code", None) != 200:
                return None
            payload = response.json()
            subject = payload.get("sub") if isinstance(payload, dict) else None
            if not isinstance(subject, str) or not subject.strip():
                return None
        except Exception:
            return None

        namespace = hashlib.sha256(
            b"nova-google-account-namespace\x00" + subject.encode("utf-8")
        ).hexdigest()[:12]
        self._account_namespace_cache = namespace
        return namespace

    def get_audit_trail(self) -> tuple[AuthAuditLog, ...]:
        """Return immutable audit records detached from internal list state."""
        return tuple(self._audit_logs)
