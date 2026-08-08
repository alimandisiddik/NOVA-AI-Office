"""Fail-closed factory for approved Google API discovery clients."""

from __future__ import annotations

from typing import Any, Callable

from app.google_workspace.auth import AuthError, GoogleAuthenticator, GoogleDependencyError


APPROVED_SERVICES: dict[str, tuple[str, ...]] = {
    "calendar": ("v3",),
    "drive": ("v3",),
    "gmail": ("v1",),
    "docs": ("v1",),
    "sheets": ("v4",),
    "slides": ("v1",),
}


def _load_discovery_builder() -> Callable[..., Any]:
    """Load the real discovery client or raise a typed dependency error."""
    try:
        from googleapiclient.discovery import build
    except ImportError as error:
        raise GoogleDependencyError(
            "Google API discovery dependency is unavailable; install "
            "google-api-python-client before enabling Google Workspace."
        ) from error
    return build


class GoogleClientFactory:
    """Create only explicitly approved, authenticated discovery clients."""

    def __init__(self, authenticator: GoogleAuthenticator):
        self._authenticator = authenticator

    def get_service(self, service_name: str, version: str) -> Any:
        approved_versions = APPROVED_SERVICES.get(service_name)
        if approved_versions is None or version not in approved_versions:
            raise GoogleDependencyError("Requested Google service is not approved")

        credentials = self._authenticator.get_credentials(require_valid=True)
        if credentials is None:
            raise AuthError("Google Workspace reconnect required")

        build = _load_discovery_builder()
        return build(service_name, version, credentials=credentials, cache_discovery=False)
