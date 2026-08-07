"""Safe, non-executing Codex specialist adapter stub."""

from __future__ import annotations

from app.providers.availability import executable_is_available
from app.providers.errors import ConnectionError
from app.providers.models import ProviderRequest, ProviderResponse


class CodexAdapter:
    """Configuration-gated stub; no CLI or shell execution occurs in Sprint 5G."""

    provider_id = "Codex"

    def __init__(self, executable_path: str = "") -> None:
        self.executable_path = executable_path.strip()

    def is_available(self) -> bool:
        return executable_is_available(self.executable_path)

    async def generate_text(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        del request, timeout_seconds
        raise ConnectionError("Codex direct provider is unavailable.")
