"""Pure, display-only mapping from registered agent identity to operator."""

from __future__ import annotations

from app.dispatch.registry import AgentRegistry


OPERATORS = frozenset({"CONTROL_TOWER", "CODEX", "CLAUDE", "GEMINI", "NINEROUTER"})


def resolve_operator(agent_id: str) -> str:
    """Resolve a registered agent to its derived operator label."""
    agent = AgentRegistry().resolve_agent(agent_id)
    if agent.adapter_id == "local_deterministic":
        return "CONTROL_TOWER"
    if agent.adapter_id == "provider_gateway":
        return {
            "coding_agent": "CODEX",
            "architecture_agent": "CLAUDE",
            "generic_ai_agent": "NINEROUTER",
        }.get(agent_id, "NINEROUTER")
    return "CONTROL_TOWER"
