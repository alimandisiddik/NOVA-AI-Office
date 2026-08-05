"""NineRouter adapter implementation for the provider gateway."""

from __future__ import annotations
import httpx
import logging
from typing import Any

from app.providers.models import ProviderRequest, ProviderResponse
from app.providers.errors import (
    AuthenticationError,
    AuthorizationError,
    ConnectionError,
    InvalidResponseError,
    ProviderError,
    RateLimitError,
    TimeoutError,
    UnsupportedOperationError,
)

LOGGER = logging.getLogger(__name__)


class NineRouterAdapter:
    """OpenAI-compatible adapter using async httpx."""

    def __init__(self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not base_url or not api_key:
            raise ValueError("base_url and api_key are required for NineRouterAdapter")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # Allow injecting mocked transport for tests
        self._transport = transport

    async def generate_text(
        self,
        request: ProviderRequest,
        *,
        timeout_seconds: float,
    ) -> ProviderResponse:
        """Call the /v1/chat/completions endpoint."""
        endpoint = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": request.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": request.prompt,
                }
            ],
            "temperature": 0,
            "stream": False,
        }

        # Initialize the client with standard security settings.
        # follow_redirects=False (cross-host redirects rejected)
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=timeout_seconds,
                follow_redirects=False
            ) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise TimeoutError("Request to NineRouter timed out.") from exc
        except httpx.RequestError as exc:
            raise ConnectionError(f"Network error connecting to NineRouter: {type(exc).__name__}") from exc

        # Handle specific HTTP status codes
        if response.status_code == 301 or response.status_code == 302 or response.status_code == 307 or response.status_code == 308:
            raise ConnectionError(f"Redirects are not permitted (HTTP {response.status_code}).")

        if response.status_code == 401:
            raise AuthenticationError("NineRouter authentication failed (401).")
        if response.status_code == 403:
            raise AuthorizationError("NineRouter authorization failed (403).")
        if response.status_code == 429:
            raise RateLimitError("NineRouter rate limit exceeded (429).")
        if response.status_code in (400, 404, 422):
            # Sanitize error body, don't expose raw
            raise UnsupportedOperationError(f"Request not supported or invalid (HTTP {response.status_code}).")
        if response.status_code >= 500:
            raise ProviderError(f"NineRouter server error (HTTP {response.status_code}).", category="provider_error")

        response.raise_for_status()

        # Parse JSON
        try:
            data = response.json()
        except ValueError as exc:
            raise InvalidResponseError("NineRouter returned invalid JSON.") from exc

        if not isinstance(data, dict):
            raise InvalidResponseError("NineRouter returned a JSON object that is not a dictionary.")

        # Validate structure based on OpenAI chat completions contract
        choices = data.get("choices")
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise InvalidResponseError("Response missing 'choices' array or empty.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise InvalidResponseError("'choices[0]' is not a dictionary.")

        message = first_choice.get("message")
        if not message or not isinstance(message, dict):
            raise InvalidResponseError("'choices[0].message' is missing or not a dictionary.")

        content = message.get("content")
        if content is None or not isinstance(content, str):
            raise InvalidResponseError("'choices[0].message.content' is missing or not a string.")

        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}

        return ProviderResponse(
            request_id=request.request_id,
            content=content,
            model_id=data.get("model", request.model_id),
            usage_prompt_tokens=usage.get("prompt_tokens", 0),
            usage_completion_tokens=usage.get("completion_tokens", 0),
            usage_total_tokens=usage.get("total_tokens", 0),
        )
