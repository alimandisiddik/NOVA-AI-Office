"""Secure provider gateway with deterministic, bounded model fallback."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional
from urllib.parse import urlparse

from app.providers.adapter import ProviderAdapter
from app.providers.errors import (
    AuthorizationError,
    CircuitOpenError,
    ConfigurationError,
    ConnectionError,
    OutputLimitError,
    ProviderError,
    RateLimitError,
    SensitiveContentError,
    TimeoutError,
)
from app.providers.models import (
    ProviderAuditRecord,
    ProviderRequest,
    ProviderRequestAttempt,
    ProviderResponse,
)
from app.providers.registry import get_registered_model
from app.providers.repository import ProviderRepository, _utc_now, hash_text
from app.providers.selection import select_eligible_models
from app.router.planner import generate_plan
from app.security import SENSITIVE_CONTENT_PATTERN

LOGGER = logging.getLogger(__name__)

MAX_TOTAL_ATTEMPTS = 3
OUTPUT_BYTE_LIMIT = 65_536
DEFAULT_TIMEOUT_SECONDS = 30.0
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_RESET_TIMEOUT = 60.0


class CircuitBreaker:
    """In-memory circuit breakers isolated by ``model_id``."""

    def __init__(self) -> None:
        self._states: dict[str, str] = {}
        self._failures: dict[str, int] = {}
        self._last_failure_times: dict[str, float] = {}

    def _ensure(self, model_id: str) -> None:
        self._states.setdefault(model_id, "closed")
        self._failures.setdefault(model_id, 0)
        self._last_failure_times.setdefault(model_id, 0.0)

    def get_state(self, model_id: str) -> str:
        self._ensure(model_id)
        return self._states[model_id]

    def is_open(self, model_id: str) -> bool:
        return self.get_state(model_id) == "open"

    def check_request_allowed(self, model_id: str) -> None:
        self._ensure(model_id)
        if self._states[model_id] != "open":
            return
        if time.monotonic() - self._last_failure_times[model_id] > CIRCUIT_BREAKER_RESET_TIMEOUT:
            self._states[model_id] = "half_open"
            return
        raise CircuitOpenError("Requested model circuit is open.")

    def record_success(self, model_id: str) -> None:
        self._ensure(model_id)
        self._states[model_id] = "closed"
        self._failures[model_id] = 0

    def record_failure(self, model_id: str) -> None:
        self._ensure(model_id)
        self._failures[model_id] += 1
        self._last_failure_times[model_id] = time.monotonic()
        if self._failures[model_id] >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            self._states[model_id] = "open"


class ProviderGatewayService:
    """Execute safe read-only generation with at most one attempt per model."""

    def __init__(
        self,
        repository: ProviderRepository,
        adapter: ProviderAdapter,
        base_url: str,
        api_key: str,
        model_priority: list[str],
        allowed_models: list[str],
    ) -> None:
        self._repo = repository
        self._adapter = adapter
        self.base_url = base_url
        self.api_key = api_key
        self.model_priority = list(model_priority)
        self.allowed_models = list(allowed_models)
        self.circuit_breaker = CircuitBreaker()
        self.last_successful_model: str | None = None
        self.last_fallback_reason: str | None = None
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.base_url or not self.api_key:
            raise ConfigurationError("NOVA_PROVIDER_BASE_URL and NOVA_PROVIDER_API_KEY must be set.")
        parsed = urlparse(self.base_url)
        if not parsed.hostname or (parsed.scheme != "https" and parsed.hostname != "localhost"):
            raise ConfigurationError("Provider base URL must use HTTPS (except localhost testing).")
        if not self.model_priority:
            raise ConfigurationError("NOVA_PROVIDER_MODEL_PRIORITY is empty or invalid.")
        if len(set(self.model_priority)) != len(self.model_priority):
            raise ConfigurationError("NOVA_PROVIDER_MODEL_PRIORITY contains duplicate model IDs.")
        if len(self.model_priority) > MAX_TOTAL_ATTEMPTS:
            raise ConfigurationError(
                f"NOVA_PROVIDER_MODEL_PRIORITY exceeds maximum attempts ({MAX_TOTAL_ATTEMPTS})."
            )
        for model_id in self.model_priority:
            if model_id not in self.allowed_models:
                raise ConfigurationError(f"Priority model '{model_id}' is not in allowed models.")
            if get_registered_model(model_id) is None:
                raise ConfigurationError(f"Priority model '{model_id}' is not registered.")

    def initialize(self) -> None:
        self._repo.initialize()

    async def generate_text(self, prompt: str, user_id: int) -> str:
        """Generate text with deterministic, same-group, structured fallback."""
        request_id = str(uuid.uuid4())
        started_at = time.monotonic()

        if SENSITIVE_CONTENT_PATTERN.search(prompt):
            self._audit(
                request_id, user_id, prompt, "GENERAL", "UNKNOWN", "failed",
                "sensitive_content_error", None, None, [], 0, None,
            )
            raise SensitiveContentError("Request rejected: sensitive content detected.")

        plan = generate_plan(prompt)
        workflow_id = plan.workflow.workflow_id
        role_id = plan.primary_roles[0].role_id if plan.primary_roles else "UNKNOWN"
        if plan.risk.risk_level == "HIGH":
            self._audit(
                request_id, user_id, prompt, workflow_id, role_id, "failed",
                "authorization_error", None, None, [], 0, None,
            )
            raise AuthorizationError("Request rejected: destructive or high-risk prompts are not permitted via gateway.")

        eligible_models = select_eligible_models(
            workflow_id,
            role_id,
            self.model_priority,
            self.allowed_models,
            self.circuit_breaker.is_open,
        )
        if not eligible_models:
            self._audit(
                request_id, user_id, prompt, workflow_id, role_id, "failed",
                "configuration_error", None, None, [], 0, None,
            )
            raise ConfigurationError("No eligible model supports this workflow and role.")

        initial_model_id = eligible_models[0]
        attempts: list[ProviderRequestAttempt] = []
        attempted_model_ids: set[str] = set()
        last_error: ProviderError | None = None
        fallback_reason: str | None = None
        response: ProviderResponse | None = None
        final_model_id: str | None = None

        for attempt_number, model_id in enumerate(eligible_models, start=1):
            if attempt_number > MAX_TOTAL_ATTEMPTS or model_id in attempted_model_ids:
                break
            attempted_model_ids.add(model_id)

            try:
                self.circuit_breaker.check_request_allowed(model_id)
            except CircuitOpenError as error:
                last_error = error
                fallback_reason = fallback_reason or error.category
                continue

            model_started_at = time.monotonic()
            request = ProviderRequest(
                request_id=request_id,
                user_id=user_id,
                provider_id="9Router",
                model_id=model_id,
                workflow_id=workflow_id,
                role_id=role_id,
                prompt=prompt,
            )
            try:
                response = await self._adapter.generate_text(request, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)
            except (ConnectionError, TimeoutError, RateLimitError) as error:
                self.circuit_breaker.record_failure(model_id)
                last_error = error
                fallback_reason = fallback_reason or error.category
                attempts.append(
                    ProviderRequestAttempt(
                        attempt_number=len(attempts) + 1,
                        model_id=model_id,
                        latency_ms=int((time.monotonic() - model_started_at) * 1000),
                        error_category=error.category,
                        status="failed",
                        created_at=_utc_now(),
                    )
                )
                continue
            except ProviderError as error:
                self.circuit_breaker.record_failure(model_id)
                last_error = error
                attempts.append(
                    ProviderRequestAttempt(
                        attempt_number=len(attempts) + 1,
                        model_id=model_id,
                        latency_ms=int((time.monotonic() - model_started_at) * 1000),
                        error_category=error.category,
                        status="failed",
                        created_at=_utc_now(),
                    )
                )
                # Generic provider errors (including unlisted 5xx) stop immediately.
                break

            self.circuit_breaker.record_success(model_id)
            final_model_id = model_id
            attempts.append(
                ProviderRequestAttempt(
                    attempt_number=len(attempts) + 1,
                    model_id=model_id,
                    latency_ms=int((time.monotonic() - model_started_at) * 1000),
                    error_category=None,
                    status="success",
                    created_at=_utc_now(),
                )
            )
            break

        if response is None:
            self.last_fallback_reason = fallback_reason
            self._audit(
                request_id,
                user_id,
                prompt,
                workflow_id,
                role_id,
                "failed",
                last_error.category if last_error else "provider_error",
                initial_model_id,
                None,
                attempts,
                0,
                fallback_reason,
            )
            if last_error is not None:
                raise last_error
            raise ProviderError("No provider model attempt completed.")

        response_size = len(response.content.encode("utf-8"))
        if response_size > OUTPUT_BYTE_LIMIT:
            self._audit(
                request_id,
                user_id,
                prompt,
                workflow_id,
                role_id,
                "failed",
                "output_limit_error",
                initial_model_id,
                final_model_id,
                attempts,
                response_size,
                fallback_reason,
            )
            raise OutputLimitError("Provider response exceeded the configured output limit.")

        self.last_successful_model = final_model_id
        self.last_fallback_reason = fallback_reason
        self._audit(
            request_id,
            user_id,
            prompt,
            workflow_id,
            role_id,
            "success",
            None,
            initial_model_id,
            final_model_id,
            attempts,
            response_size,
            fallback_reason,
        )
        return response.content

    def _audit(
        self,
        request_id: str,
        user_id: int,
        prompt: str,
        workflow_id: str,
        role_id: str,
        status: str,
        error_category: str | None,
        initial_model_id: str | None,
        final_model_id: str | None,
        attempts: list[ProviderRequestAttempt],
        response_size: int,
        fallback_reason: str | None,
    ) -> None:
        try:
            self._repo.log_request(
                ProviderAuditRecord(
                    request_id=request_id,
                    execution_id=None,
                    user_id=user_id,
                    provider_id="9Router",
                    model_id=final_model_id or initial_model_id or "UNKNOWN",
                    workflow_id=workflow_id,
                    role_id=role_id,
                    status=status,
                    prompt_hash=hash_text(prompt),
                    response_size=response_size,
                    latency_ms=0,
                    retry_count=0,
                    error_category=error_category,
                    created_at=_utc_now(),
                    completed_at=_utc_now(),
                    initial_model_id=initial_model_id,
                    final_model_id=final_model_id,
                    attempt_count=len(attempts),
                    fallback_used=int(len(attempts) > 1),
                    fallback_reason=fallback_reason,
                ),
                attempts,
            )
        except Exception:
            LOGGER.exception("Provider audit write failed.")
