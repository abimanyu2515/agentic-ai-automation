"""
Two layers, tested at their own boundary:

1. LLMService <-> litellm - using litellm's real mock_response feature.
   Exercises the actual integration code (model string, response parsing,
   error wrapping) with zero network calls and no API key.

2. ScenarioAgent <-> LLMService - using StubLLMService (conftest.py), a
   test double that never touches litellm. Verifies the agent's own logic
   (prompt building, mapping LLM output onto Scenario objects) without
   depending on the LLM integration layer at all.
"""

import pytest

from backend.agents.scenario_agent import ScenarioAgent, _GeneratedScenarioBatch
from backend.config.settings import LLMConfig
from backend.models.requirement import Requirement
from backend.models.state import WorkflowState
from backend.services.llm import LLMService, LLMServiceError

_TEST_LLM_CONFIG = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="fake-key-for-test")


def test_llm_service_structured_generate_parses_mock_response():
    service = LLMService(config=_TEST_LLM_CONFIG)

    result = service.structured_generate(
        user="irrelevant for this test - mock_response short-circuits the call",
        response_model=_GeneratedScenarioBatch,
        mock_response='{"scenarios": [{"scenario_name": "Valid Login", "description": "desc", "priority": "high"}]}',
    )

    assert len(result.scenarios) == 1
    assert result.scenarios[0].scenario_name == "Valid Login"


def test_llm_service_raises_on_schema_mismatch():
    """A response that doesn't match the schema must fail loudly, not
    silently return an empty or partial result."""
    service = LLMService(config=_TEST_LLM_CONFIG)

    with pytest.raises(LLMServiceError):
        service.structured_generate(
            user="irrelevant",
            response_model=_GeneratedScenarioBatch,
            mock_response='{"totally": "wrong shape"}',
        )


def test_scenario_agent_maps_llm_output_to_scenarios(stub_llm_service):
    agent = ScenarioAgent(llm_service=stub_llm_service)
    requirement = Requirement(title="Login flow", description="User can log in with valid credentials")
    state = WorkflowState(requirement=requirement)

    result = agent.run(state)

    assert len(result.generated_scenarios) == 3
    assert result.generated_scenarios[0].scenario_name == "Valid Login"
    assert result.generated_scenarios[0].requirement_id == requirement.id
    assert "Scenario Agent generated 3 scenarios" in result.logs[-1]


def test_scenario_agent_requires_requirement():
    agent = ScenarioAgent(llm_service=None)  # never called - the guard fires first
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)