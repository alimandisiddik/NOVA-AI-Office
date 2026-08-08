"""Static, fail-closed registry of approved NOVA agents."""

from __future__ import annotations

from dataclasses import dataclass

from app.dispatch.errors import UnknownAgentError, UnsupportedCapabilityError


@dataclass(frozen=True)
class RegisteredAgent:
    agent_id: str
    display_name: str
    category: str
    capabilities: frozenset[str]
    adapter_id: str


AGENT_REGISTRY: dict[str, RegisteredAgent] = {
    "document_agent": RegisteredAgent("document_agent", "Document Agent", "document", frozenset({"read_only", "draft_only"}), "local_deterministic"),
    "presentation_agent": RegisteredAgent("presentation_agent", "Presentation Agent", "presentation", frozenset({"read_only", "draft_only"}), "local_deterministic"),
    "procurement_agent": RegisteredAgent("procurement_agent", "Procurement Agent", "procurement", frozenset({"read_only", "draft_only", "external_communication"}), "local_deterministic"),
    "policy_agent": RegisteredAgent("policy_agent", "Policy Agent", "policy", frozenset({"read_only", "draft_only", "publication"}), "local_deterministic"),
    "academic_agent": RegisteredAgent("academic_agent", "Academic Agent", "academic", frozenset({"read_only", "draft_only"}), "local_deterministic"),
    "development_agent": RegisteredAgent("development_agent", "Development Agent", "development", frozenset({"read_only", "draft_only"}), "local_deterministic"),
    "workspace_agent": RegisteredAgent("workspace_agent", "Workspace Agent", "workspace", frozenset({"read_only", "draft_only", "workspace_write"}), "google_workspace_adapter"),
    "night_shift_agent": RegisteredAgent("night_shift_agent", "Night Shift Agent", "night_shift", frozenset({"read_only", "draft_only"}), "local_deterministic"),
    # Sprint 5G provider-backed agents
    "coding_agent": RegisteredAgent("coding_agent", "Coding Agent", "development", frozenset({"read_only", "draft_only"}), "provider_gateway"),
    "architecture_agent": RegisteredAgent("architecture_agent", "Architecture Agent", "development", frozenset({"read_only", "draft_only"}), "provider_gateway"),
    "generic_ai_agent": RegisteredAgent("generic_ai_agent", "Generic AI Agent", "general", frozenset({"read_only", "draft_only"}), "provider_gateway"),
}


class AgentRegistry:
    def resolve_agent(self, agent_id: str) -> RegisteredAgent:
        try:
            return AGENT_REGISTRY[agent_id]
        except KeyError as error:
            raise UnknownAgentError("Agent is not registered.") from error

    def validate_capability(self, agent_id: str, capability: str) -> RegisteredAgent:
        agent = self.resolve_agent(agent_id)
        if capability not in agent.capabilities:
            raise UnsupportedCapabilityError("Agent capability is not supported.")
        return agent

    def list_agents(self, *, category: str | None = None) -> list[RegisteredAgent]:
        agents = list(AGENT_REGISTRY.values())
        if category is not None:
            agents = [agent for agent in agents if agent.category == category]
        return sorted(agents, key=lambda agent: agent.agent_id)
