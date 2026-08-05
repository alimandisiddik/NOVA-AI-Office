"""Provider gateway service orchestrating safe provider calls."""

from __future__ import annotations
import uuid
import time
import logging
from dataclasses import dataclass
from typing import Optional, Any
from urllib.parse import urlparse

from app.providers.models import ProviderRequest, ProviderResponse, ProviderAuditRecord
from app.providers.adapter import ProviderAdapter
from app.providers.repository import ProviderRepository, hash_text, _utc_now
from app.providers.errors import (
    AuthenticationError,
    AuthorizationError,
    CircuitOpenError,
    ConfigurationError,
    ConnectionError,
    InvalidResponseError,
    OutputLimitError,
    ProviderError,
    RateLimitError,
    SensitiveContentError,
    TimeoutError,
    UnsupportedOperationError,
)
from app.router.classifier import classify_intent
from app.router.risk import assess_risk
from app.router.planner import generate_plan
from app.security import SENSITIVE_CONTENT_PATTERN


LOGGER = logging.getLogger(__name__)

MAX_RETRIES = 2
OUTPUT_BYTE_LIMIT = 65_536  # Same as execution service limit
DEFAULT_TIMEOUT_SECONDS = 30.0
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_RESET_TIMEOUT = 60.0


class CircuitBreaker:
    """In-memory circuit breaker for Sprint 4A."""

    def __init__(self) -> None:
        self.state = "closed"
        self.failures = 0
        self.last_failure_time = 0.0

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure_time = time.monotonic()
        if self.state == "closed" and self.failures >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            self.state = "open"
            LOGGER.warning("Circuit breaker transitioning to OPEN state.")

    def record_success(self) -> None:
        if self.state == "half_open":
            self.state = "closed"
            self.failures = 0
            LOGGER.info("Circuit breaker transitioning to CLOSED state.")
        elif self.state == "closed":
            self.failures = 0

    def check_request_allowed(self) -> None:
        if self.state == "open":
            if time.monotonic() - self.last_failure_time > CIRCUIT_BREAKER_RESET_TIMEOUT:
                self.state = "half_open"
                LOGGER.info("Circuit breaker transitioning to HALF_OPEN state.")
            else:
                raise CircuitOpenError("Circuit breaker is open. Requests blocked.")


class ProviderGatewayService:
    """Orchestrates provider calls with strict security, retry, and auditing."""

    def __init__(
        self,
        repository: ProviderRepository,
        adapter: ProviderAdapter,
        base_url: str,
        api_key: str,
        default_model: str,
        allowed_models: list[str],
    ) -> None:
        self._repo = repository
        self._adapter = adapter
        self.base_url = base_url
        self.api_key = api_key
        self.default_model = default_model
        self.allowed_models = allowed_models

        self.circuit_breaker = CircuitBreaker()
        self._validate_config()

    def _validate_config(self) -> None:
        if not self.base_url or not self.api_key:
            raise ConfigurationError("NOVA_PROVIDER_BASE_URL and NOVA_PROVIDER_API_KEY must be set.")
        if not self.base_url.startswith("https://") and not self.base_url.startswith("http://localhost"):
            raise ConfigurationError("Provider base URL must use HTTPS (except localhost testing).")
        if not self.default_model or self.default_model not in self.allowed_models:
            raise ConfigurationError(f"Default model '{self.default_model}' is not in allowed models.")
        try:
            parsed = urlparse(self.base_url)
            if not parsed.hostname:
                raise ConfigurationError("Invalid provider URL.")
        except Exception as exc:
            raise ConfigurationError("Could not parse provider URL.") from exc

    def initialize(self) -> None:
        """Initialize the additive provider audit schema."""
        self._repo.initialize()

    async def generate_text(self, prompt: str, user_id: int) -> str:
        """Main entry point for text generation. Generates a correlation ID and handles logic."""
        request_id = str(uuid.uuid4())

        # 1. Reject sensitive content
        if SENSITIVE_CONTENT_PATTERN.search(prompt):
            self._log_failure(
                request_id, user_id, prompt, "sensitive_content_error", 0, 0, "GENERAL", "UNKNOWN", "UNKNOWN"
            )
            raise SensitiveContentError("Request rejected: sensitive content detected.")

        # 2. Reject destructive/high-risk requests via router
        plan = generate_plan(prompt)
        workflow_id = plan.workflow.workflow_id

        # Resolve role based on workflow. If general, use CONTROL_TOWER.
        role_id = plan.primary_roles[0].role_id if plan.primary_roles else "UNKNOWN"

        if plan.risk.risk_level == "HIGH":
            self._log_failure(
                request_id, user_id, prompt, "authorization_error", 0, 0, workflow_id, role_id, "UNKNOWN"
            )
            raise AuthorizationError("Request rejected: destructive or high-risk prompts are not permitted via gateway.")

        # 3. Model selection (deterministic based on config for now, can be role-based later)
        model_id = self.default_model
        if model_id not in self.allowed_models:
            model_id = self.allowed_models[0]

        # 4. Check circuit breaker
        self.circuit_breaker.check_request_allowed()

        # Build request
        req = ProviderRequest(
            request_id=request_id,
            user_id=user_id,
            provider_id="9Router",
            model_id=model_id,
            workflow_id=workflow_id,
            role_id=role_id,
            prompt=prompt,
            execution_id=None,
        )

        # 5. Execute with bounded retries
        retries = 0
        start_time = time.monotonic()
        response: Optional[ProviderResponse] = None
        error_cat: Optional[str] = None

        while retries <= MAX_RETRIES:
            try:
                response = await self._adapter.generate_text(req, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)
                self.circuit_breaker.record_success()
                break
            except (ConnectionError, TimeoutError, RateLimitError) as exc:
                # These are retryable
                self.circuit_breaker.record_failure()
                error_cat = exc.category
                if retries < MAX_RETRIES:
                    retries += 1
                    # Exponential backoff would go here, but for Sprint 4A a tiny async sleep is ok,
                    # or just immediately retry.
                    continue
                else:
                    self._log_failure(
                        request_id, user_id, prompt, error_cat,
                        int((time.monotonic() - start_time) * 1000), retries,
                        workflow_id, role_id, model_id
                    )
                    raise
            except ProviderError as exc:
                # E.g., 500 ProviderError is retryable. But AuthenticationError is not.
                self.circuit_breaker.record_failure()
                error_cat = exc.category
                # Do not retry 400, 401, 403, invalid model, unsupported, invalid json
                if exc.category in (
                    "authentication_error", "authorization_error",
                    "invalid_response", "unsupported_operation",
                    "sensitive_content_error"
                ):
                    self._log_failure(
                        request_id, user_id, prompt, error_cat,
                        int((time.monotonic() - start_time) * 1000), retries,
                        workflow_id, role_id, model_id
                    )
                    raise

                # Assume other provider_error (5xx) is retryable
                if retries < MAX_RETRIES:
                    retries += 1
                    continue
                else:
                    self._log_failure(
                        request_id, user_id, prompt, error_cat,
                        int((time.monotonic() - start_time) * 1000), retries,
                        workflow_id, role_id, model_id
                    )
                    raise
            except Exception as exc:
                # Unknown error
                self.circuit_breaker.record_failure()
                self._log_failure(
                    request_id, user_id, prompt, "provider_error",
                    int((time.monotonic() - start_time) * 1000), retries,
                    workflow_id, role_id, model_id
                )
                raise ProviderError(f"Unexpected error: {exc}") from exc

        latency_ms = int((time.monotonic() - start_time) * 1000)

        if not response:
            raise ProviderError("Failed to generate text.")

        response_bytes = len(response.content.encode("utf-8"))
        if response_bytes > OUTPUT_BYTE_LIMIT:
            self._log_failure(
                request_id, user_id, prompt, "output_limit_error",
                latency_ms, retries, workflow_id, role_id, model_id, response_size=response_bytes
            )
            raise OutputLimitError(f"Response size ({response_bytes} bytes) exceeds limit ({OUTPUT_BYTE_LIMIT} bytes).")

        # Log success
        audit = ProviderAuditRecord(
            request_id=request_id,
            execution_id=None,
            user_id=user_id,
            provider_id="9Router",
            model_id=model_id,
            workflow_id=workflow_id,
            role_id=role_id,
            status="success",
            prompt_hash=hash_text(prompt),
            response_size=response_bytes,
            latency_ms=latency_ms,
            retry_count=retries,
            error_category=None,
            created_at=_utc_now(),
            completed_at=_utc_now(),
        )
        self._repo.log_request(audit)

        return response.content

    def _log_failure(
        self, request_id: str, user_id: int, prompt: str, error_category: str,
        latency_ms: int, retry_count: int, workflow_id: str, role_id: str,
        model_id: str, response_size: int = 0
    ) -> None:
        try:
            audit = ProviderAuditRecord(
                request_id=request_id,
                execution_id=None,
                user_id=user_id,
                provider_id="9Router",
                model_id=model_id,
                workflow_id=workflow_id,
                role_id=role_id,
                status="failed",
                prompt_hash=hash_text(prompt),
                response_size=response_size,
                latency_ms=latency_ms,
                retry_count=retry_count,
                error_category=error_category,
                created_at=_utc_now(),
                completed_at=_utc_now(),
            )
            self._repo.log_request(audit)
        except Exception as exc:
            LOGGER.error("Failed to write failure audit log: %s", exc)
