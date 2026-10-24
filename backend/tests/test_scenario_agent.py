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

from types import SimpleNamespace

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
        mock_response='{"scenarios": [{"scenario_name": "Valid Login", "description": "desc", "priority": "high", "path_type": "happy_path"}]}',
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


def test_llm_service_reports_provider_timeout_as_call_failure(monkeypatch):
    def timeout_completion(**kwargs):
        raise TimeoutError("Request timed out")

    monkeypatch.setattr("backend.services.llm.completion", timeout_completion)
    service = LLMService(config=_TEST_LLM_CONFIG)

    with pytest.raises(
        LLMServiceError,
        match=r"LLM call failed.*Request timed out.*1 attempt.*30s timeout",
    ):
        service.structured_generate(
            user="test",
            response_model=_GeneratedScenarioBatch,
            max_retries=1,
            timeout=30,
        )


def test_llm_service_reports_empty_model_content(monkeypatch):
    monkeypatch.setattr(
        "backend.services.llm.completion",
        lambda **kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None),
                    finish_reason="length",
                )
            ]
        ),
    )
    service = LLMService(config=_TEST_LLM_CONFIG)

    with pytest.raises(
        LLMServiceError,
        match="empty response content.*finish_reason='length'.*1 attempt",
    ):
        service.structured_generate(
            user="test",
            response_model=_GeneratedScenarioBatch,
            max_retries=1,
        )


def test_nvidia_llm_service_defaults_to_nvidia_nim_and_keeps_custom_base():
    default_service = LLMService(
        config=LLMConfig(
            provider="nvidia",
            model="deepseek-ai/deepseek-v4.1-flash",
            api_key="fake-key-for-test",
        )
    )
    custom_service = LLMService(
        config=LLMConfig(
            provider="nvidia",
            model="deepseek-ai/deepseek-v4.1-flash",
            api_key="fake-key-for-test",
            api_base="https://nvidia.example.test/v1",
        )
    )

    assert default_service._api_base() == "https://integrate.api.nvidia.com/v1"
    assert custom_service._api_base() == "https://nvidia.example.test/v1"


def test_scenario_agent_maps_llm_output_to_scenarios(stub_llm_service):
    agent = ScenarioAgent(llm_service=stub_llm_service)
    requirement = Requirement(title="Login flow", description="User can log in with valid credentials")
    state = WorkflowState(requirement=requirement)

    result = agent.run(state)

    assert len(result.generated_scenarios) == 3
    assert result.generated_scenarios[0].scenario_name == "Valid Login"
    assert result.generated_scenarios[0].requirement_id == requirement.id
    assert "Scenario Agent generated 3 scenarios" in result.logs[-1]


def test_scenario_agent_uses_bounded_timeout_without_nested_provider_retries(
    stub_llm_service,
):
    class RecordingLLMService:
        def __init__(self):
            self.options = None

        def structured_generate(self, *, user, response_model, **kwargs):
            self.options = kwargs
            return stub_llm_service.structured_generate(
                user=user,
                response_model=response_model,
            )

    llm_service = RecordingLLMService()
    state = WorkflowState(
        requirement=Requirement(
            title="Login flow",
            description="User can log in with valid credentials",
        )
    )

    ScenarioAgent(llm_service=llm_service).run(state)

    assert llm_service.options == {
        "timeout": 300,
        "max_retries": 1,
        "num_retries": 0,
        "max_tokens": 4096,
    }


def test_scenario_agent_requires_requirement():
    agent = ScenarioAgent(llm_service=None)  # never called - the guard fires first
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)