import pytest
from app.dispatch.registry import AgentRegistry, AGENT_REGISTRY
from app.dispatch.errors import UnknownAgentError, UnsupportedCapabilityError

@pytest.mark.parametrize("agent_id", sorted(AGENT_REGISTRY))
def test_resolve_every_registered_agent(agent_id):
    agent = AgentRegistry().resolve_agent(agent_id)
    assert agent.agent_id == agent_id
    assert agent.capabilities

def test_resolve_unknown_agent_fails():
    registry = AgentRegistry()
    with pytest.raises(UnknownAgentError):
        registry.resolve_agent("hacker_agent")

def test_validate_capability_success():
    registry = AgentRegistry()
    registry.validate_capability("document_agent", "draft_only")

def test_validate_capability_unsupported():
    registry = AgentRegistry()
    with pytest.raises(UnsupportedCapabilityError):
        registry.validate_capability("document_agent", "external_communication")

def test_no_git_mutation_paid_action_in_registry():
    for agent_id, agent in AGENT_REGISTRY.items():
        assert "git_mutation" not in agent.capabilities, f"Agent {agent_id} has prohibited git_mutation capability"
        assert "paid_action" not in agent.capabilities, f"Agent {agent_id} has prohibited paid_action capability"

def test_list_agents_is_deterministic():
    registry = AgentRegistry()
    agents = registry.list_agents()
    assert [agent.agent_id for agent in agents] == sorted(agent.agent_id for agent in agents)
    assert len(agents) == 8
