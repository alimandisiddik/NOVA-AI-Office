"""Secure provider gateway with deterministic, bounded provider fallback."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from urllib.parse import urlparse

from app.providers.adapter import ProviderAdapter
from app.providers.errors import AuthorizationError, CircuitOpenError, ConfigurationError, ConnectionError, OutputLimitError, ProviderError, SensitiveContentError
from app.providers.models import ProviderAuditRecord, ProviderRequest, ProviderRequestAttempt, ProviderResponse
from app.providers.outcomes import ProviderOutcome, is_fallback_eligible, outcome_for_error
from app.providers.registry import get_registered_model
from app.providers.repository import ProviderRepository, _utc_now, hash_text
from app.providers.selection import select_provider_chain
from app.router.planner import generate_plan
from app.security import SENSITIVE_CONTENT_PATTERN

LOGGER = logging.getLogger(__name__)

MAX_TOTAL_ATTEMPTS = 3
OUTPUT_BYTE_LIMIT = 65_536
DEFAULT_TIMEOUT_SECONDS = 30.0
CIRCUIT_BREAKER_FAILURE_THRESHOLD = 5
CIRCUIT_BREAKER_RESET_TIMEOUT = 60.0


class CircuitBreaker:
    """In-memory circuits keyed by the provider-qualified route alias."""

    def __init__(self) -> None:
        self._states: dict[str, str] = {}
        self._failures: dict[str, int] = {}
        self._last_failure_times: dict[str, float] = {}

    def _ensure(self, key: str) -> None:
        self._states.setdefault(key, "closed")
        self._failures.setdefault(key, 0)
        self._last_failure_times.setdefault(key, 0.0)

    def get_state(self, key: str) -> str:
        self._ensure(key)
        return self._states[key]

    def is_open(self, key: str) -> bool:
        return self.get_state(key) == "open"

    def check_request_allowed(self, key: str) -> None:
        self._ensure(key)
        if self._states[key] != "open":
            return
        if time.monotonic() - self._last_failure_times[key] > CIRCUIT_BREAKER_RESET_TIMEOUT:
            self._states[key] = "half_open"
            return
        raise CircuitOpenError("Provider route circuit is open.")

    def record_success(self, key: str) -> None:
        self._ensure(key)
        self._states[key] = "closed"
        self._failures[key] = 0

    def record_failure(self, key: str) -> None:
        self._ensure(key)
        self._failures[key] += 1
        self._last_failure_times[key] = time.monotonic()
        if self._failures[key] >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
            self._states[key] = "open"


class ProviderGatewayService:
    """Single provider execution entry point with a maximum of three live calls."""

    def __init__(
        self,
        repository: ProviderRepository,
        adapter: ProviderAdapter | Mapping[str, ProviderAdapter],
        base_url: str,
        api_key: str,
        model_priority: list[str],
        allowed_models: list[str],
        *,
        combo_priorities: Mapping[str, list[str]] | None = None,
    ) -> None:
        self._repo = repository
        self._adapters = {"9Router": adapter} if not isinstance(adapter, Mapping) else dict(adapter)
        self.base_url = base_url
        self.api_key = api_key
        self.model_priority = list(model_priority)
        self.allowed_models = list(allowed_models)
        self.combo_priorities = {"generic": list(model_priority)}
        if combo_priorities:
            self.combo_priorities.update({name: list(priority) for name, priority in combo_priorities.items()})
        self.circuit_breaker = CircuitBreaker()
        self.last_successful_model: str | None = None
        self.last_fallback_reason: str | None = None
        self._validate_config()

    def _get_adapter(self, provider_id: str) -> ProviderAdapter | None:
        return self._adapters.get(provider_id)

    def _configured_specialists(self) -> frozenset[str]:
        return frozenset(provider_id for provider_id in ("Codex", "Claude") if provider_id in self._adapters)

    def _validate_config(self) -> None:
        if not self.base_url or not self.api_key:
            raise ConfigurationError("NOVA_PROVIDER_BASE_URL and NOVA_PROVIDER_API_KEY must be set.")
        parsed = urlparse(self.base_url)
        if not parsed.hostname or (parsed.scheme != "https" and parsed.hostname != "localhost"):
            raise ConfigurationError("Provider base URL must use HTTPS (except localhost testing).")
        if not self.model_priority:
            raise ConfigurationError("NOVA_PROVIDER_MODEL_PRIORITY is empty or invalid.")
        for group, priority in self.combo_priorities.items():
            if len(priority) > MAX_TOTAL_ATTEMPTS:
                raise ConfigurationError(f"Provider priority for '{group}' exceeds maximum attempts ({MAX_TOTAL_ATTEMPTS}).")
            if len(priority) != len(set(priority)):
                raise ConfigurationError(f"Provider priority for '{group}' contains duplicate model IDs.")
            for model_id in priority:
                if model_id not in self.allowed_models or get_registered_model(model_id) is None:
                    raise ConfigurationError(f"Priority model '{model_id}' is not allowed and registered.")

    def initialize(self) -> None:
        self._repo.initialize()

    async def generate_text(self, prompt: str, user_id: int, *, workflow_id: str | None = None, role_id: str | None = None) -> str:
        request_id = str(uuid.uuid4())
        if SENSITIVE_CONTENT_PATTERN.search(prompt):
            self._audit(request_id, user_id, prompt, "GENERAL", "UNKNOWN", "failed", "sensitive_content_error", None, None, [], 0, None)
            raise SensitiveContentError("Request rejected: sensitive content detected.")

        plan = generate_plan(prompt)
        workflow_id = workflow_id or plan.workflow.workflow_id
        role_id = role_id or (plan.primary_roles[0].role_id if plan.primary_roles else "UNKNOWN")
        if plan.risk.risk_level == "HIGH":
            self._audit(request_id, user_id, prompt, workflow_id, role_id, "failed", "authorization_error", None, None, [], 0, None)
            raise AuthorizationError("Request rejected: destructive or high-risk prompts are not permitted via gateway.")

        chain = select_provider_chain(
            workflow_id, role_id, combo_priorities=self.combo_priorities,
            allowed_models=self.allowed_models, configured_specialists=self._configured_specialists(),
        )
        if not chain:
            self._audit(request_id, user_id, prompt, workflow_id, role_id, "failed", "configuration_error", None, None, [], 0, None)
            raise ConfigurationError("No eligible provider route supports this workflow and role.")

        initial = chain[0]
        attempts: list[ProviderRequestAttempt] = []
        live_attempts = 0
        last_error: ProviderError | None = None
        last_outcome: ProviderOutcome | None = None
        fallback_reason: str | None = None
        response: ProviderResponse | None = None
        final_provider_id: str | None = None
        final_model_id: str | None = None

        for candidate in chain:
            if live_attempts >= MAX_TOTAL_ATTEMPTS:
                break
            circuit_key = f"{candidate.provider_id}:{candidate.model_id}"
            adapter = self._get_adapter(candidate.provider_id)
            if adapter is None or not adapter.is_available():
                last_error = ConnectionError("Provider route is unavailable.")
                last_outcome = ProviderOutcome.PROVIDER_UNAVAILABLE
                fallback_reason = fallback_reason or last_outcome.value
                attempts.append(ProviderRequestAttempt(len(attempts) + 1, candidate.model_id, 0, last_outcome.value, "skipped", _utc_now(), candidate.provider_id))
                continue
            try:
                self.circuit_breaker.check_request_allowed(circuit_key)
            except CircuitOpenError as error:
                last_error = error
                last_outcome = ProviderOutcome.PROVIDER_UNAVAILABLE
                fallback_reason = fallback_reason or last_outcome.value
                attempts.append(ProviderRequestAttempt(len(attempts) + 1, candidate.model_id, 0, last_outcome.value, "skipped", _utc_now(), candidate.provider_id))
                continue

            live_attempts += 1
            started_at = time.monotonic()
            request = ProviderRequest(request_id, user_id, candidate.provider_id, candidate.model_id, workflow_id, role_id, prompt)
            try:
                response = await adapter.generate_text(request, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)
            except ProviderError as error:
                self.circuit_breaker.record_failure(circuit_key)
                last_error = error
                last_outcome = outcome_for_error(error)
                fallback_reason = fallback_reason or error.category
                attempts.append(ProviderRequestAttempt(len(attempts) + 1, candidate.model_id, int((time.monotonic() - started_at) * 1000), last_outcome.value, "failed", _utc_now(), candidate.provider_id))
                if is_fallback_eligible(last_outcome):
                    continue
                break
            except Exception as error:
                # An adapter bug (not a typed ProviderError) must still be
                # visible in operator logs -- only the exception's class name
                # is logged, matching this codebase's established safe-log
                # convention (see DispatchService.dispatch()); never the raw
                # message, which could echo prompt content back verbatim.
                LOGGER.error(
                    "provider_gateway_attempt event=adapter_internal_error provider_id=%s model_id=%s category=%s",
                    candidate.provider_id, candidate.model_id, type(error).__name__,
                )
                self.circuit_breaker.record_failure(circuit_key)
                last_error = ProviderError("Provider execution failed.")
                last_outcome = ProviderOutcome.MODEL_ERROR
                attempts.append(ProviderRequestAttempt(len(attempts) + 1, candidate.model_id, int((time.monotonic() - started_at) * 1000), last_outcome.value, "failed", _utc_now(), candidate.provider_id))
                break

            self.circuit_breaker.record_success(circuit_key)
            final_provider_id, final_model_id = candidate.provider_id, candidate.model_id
            attempts.append(ProviderRequestAttempt(len(attempts) + 1, candidate.model_id, int((time.monotonic() - started_at) * 1000), None, "success", _utc_now(), candidate.provider_id))
            break

        if response is None:
            self.last_fallback_reason = fallback_reason
            self._audit(request_id, user_id, prompt, workflow_id, role_id, "failed", (last_outcome or ProviderOutcome.MODEL_ERROR).value, initial.model_id, None, attempts, 0, fallback_reason, initial.provider_id, None, None, live_attempts)
            if live_attempts == 0 and attempts and all(attempt.status == "skipped" for attempt in attempts):
                raise ConfigurationError("No eligible provider route supports this workflow and role.")
            raise last_error or ProviderError("No provider route completed.")

        response_size = len(response.content.encode("utf-8"))
        if response_size > OUTPUT_BYTE_LIMIT:
            self._audit(request_id, user_id, prompt, workflow_id, role_id, "failed", ProviderOutcome.MODEL_ERROR.value, initial.model_id, final_model_id, attempts, response_size, fallback_reason, initial.provider_id, final_provider_id, response.model_id, live_attempts)
            raise OutputLimitError("Provider response exceeded the configured output limit.")

        self.last_successful_model = final_model_id
        self.last_fallback_reason = fallback_reason
        self._audit(request_id, user_id, prompt, workflow_id, role_id, "success", None, initial.model_id, final_model_id, attempts, response_size, fallback_reason, initial.provider_id, final_provider_id, response.model_id, live_attempts)
        return response.content

    def _audit(self, request_id: str, user_id: int, prompt: str, workflow_id: str, role_id: str, status: str, error_category: str | None, initial_model_id: str | None, final_model_id: str | None, attempts: list[ProviderRequestAttempt], response_size: int, fallback_reason: str | None, initial_provider_id: str | None = None, final_provider_id: str | None = None, resolved_model_label: str | None = None, live_attempts: int = 0) -> None:
        try:
            self._repo.log_request(ProviderAuditRecord(request_id, None, user_id, final_provider_id or initial_provider_id or "9Router", final_model_id or initial_model_id or "UNKNOWN", workflow_id, role_id, status, hash_text(prompt), response_size, 0, 0, error_category, _utc_now(), _utc_now(), initial_model_id, final_model_id, live_attempts, int(live_attempts > 1), fallback_reason, initial_provider_id, final_provider_id, resolved_model_label), attempts)
        except Exception:
            LOGGER.exception("Provider audit write failed.")
