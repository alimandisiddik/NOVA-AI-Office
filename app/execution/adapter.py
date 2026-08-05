"""LocalDeterministicAdapter — the only execution adapter in Sprint 3.

Constraints (enforced here and documented for future adapters)
-------------------------------------------------------------
shell            = False   — never use shell=True in any subprocess call
argv-only        = True    — only argv lists, never shell strings
no subprocess    = True    — this adapter makes zero subprocess calls
no shell         = True    — no os.system, popen, or shell invocation
no network       = True    — no socket, requests, httpx, or urllib call
no provider      = True    — no OpenAI, Gemini, Claude, Codex SDK
no repository    = True    — does not execute inside the NOVA repo
no user paths    = True    — paths constructed only from integer execution IDs

Environment allowlist for future real adapters (NOT used here):
  - PATH (restricted)
  - LANG
  - LC_ALL
  Exclude: SSH_AUTH_SOCK, SSH_AGENT_PID, all provider keys/tokens/credentials,
           all host secrets, all inherited host environment variables not listed.

Path safety rule for future real adapters:
  - Build artifact paths only from trusted integer execution IDs.
  - Never use user-supplied instruction text, project names, or usernames
    as a path component.
  - Always resolve and assert the path remains within the configured root.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


MAX_OUTPUT_BYTES: int = 65_536   # 64 KiB hard limit on simulated output
ADAPTER_TIMEOUT_SECONDS: int = 30  # logical timeout threshold


@dataclass(frozen=True)
class AdapterResult:
    """The outcome of a local deterministic adapter run."""

    success: bool
    summary: str       # safe, non-sensitive; max 512 chars
    output_bytes: int  # simulated byte count for limit testing


class LocalDeterministicAdapter:
    """In-process deterministic adapter — no shell, no subprocess, no provider.

    This adapter exists only to prove the orchestration lifecycle works
    end-to-end.  It produces a predictable, safe result from the
    instruction hash alone.

    Future real adapters must follow all constraints listed in this module's
    docstring before activation.
    """

    def __init__(
        self,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
        timeout_seconds: int = ADAPTER_TIMEOUT_SECONDS,
    ) -> None:
        self._max_output_bytes = max_output_bytes
        self._timeout_seconds = timeout_seconds

    def run(self, execution_id: int, instruction_hash: str) -> AdapterResult:
        """Execute deterministically from the instruction hash only.

        Parameters
        ----------
        execution_id:
            Trusted integer ID — never user-supplied text.
        instruction_hash:
            SHA-256 hex digest of the original instruction.  The raw
            instruction is never passed to or stored by this method.

        Returns
        -------
        AdapterResult
            A predictable, immutable result.  Success is true for all
            non-empty hashes in this adapter (real adapters would differ).
        """
        if not instruction_hash or len(instruction_hash) != 64:
            return AdapterResult(
                success=False,
                summary="Invalid instruction hash — execution aborted.",
                output_bytes=0,
            )

        # Deterministic simulation: derive a small output size from the hash
        # so tests can vary it without external calls.
        simulated_bytes = int(instruction_hash[:8], 16) % 1024  # 0–1023 bytes

        if simulated_bytes > self._max_output_bytes:
            return AdapterResult(
                success=False,
                summary=(
                    f"Output limit exceeded: {simulated_bytes} bytes > "
                    f"{self._max_output_bytes} bytes limit."
                ),
                output_bytes=simulated_bytes,
            )

        summary = (
            f"LocalDeterministicAdapter completed execution #{execution_id}. "
            f"Hash prefix: {instruction_hash[:8]}. "
            f"Simulated output: {simulated_bytes} bytes."
        )
        return AdapterResult(
            success=True,
            summary=summary,
            output_bytes=simulated_bytes,
        )

    @staticmethod
    def simulate_timeout() -> AdapterResult:
        """Return a failure result representing a timeout condition."""
        return AdapterResult(
            success=False,
            summary="Execution timed out before completion.",
            output_bytes=0,
        )

    @staticmethod
    def simulate_output_limit(bytes_produced: int, limit: int) -> AdapterResult:
        """Return a failure result representing an output-limit violation."""
        return AdapterResult(
            success=False,
            summary=f"Output limit exceeded: {bytes_produced} bytes > {limit} bytes.",
            output_bytes=bytes_produced,
        )


def hash_instruction(raw_instruction: str) -> str:
    """Return the SHA-256 hex digest of a raw instruction string.

    The raw instruction must never be stored or logged directly.
    Only this digest is persisted in the executions table.
    """
    return hashlib.sha256(raw_instruction.encode("utf-8")).hexdigest()
