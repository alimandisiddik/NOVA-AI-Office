"""Provider adapter protocol."""

from __future__ import annotations
from typing import Protocol

from app.providers.models import ProviderRequest, ProviderResponse


class ProviderAdapter(Protocol):
    """Asynchronous protocol for provider integrations."""

    async def generate_text(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        """Generate text from a prompt asynchronously."""
        ...
