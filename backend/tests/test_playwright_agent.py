"""
Two layers, same approach as every other agent:
1. LLMService.generate() <-> litellm - real mock_response. This is the
   FIRST test of generate() at all - every prior agent used
   structured_generate(), so this method has been sitting untested since
   it was written for the ScenarioAgent round.
2. PlaywrightAgent <-> LLMService - stub-based (see conftest.py), proving
   this agent only ever sees approved_test_cases(), never the rejected or
   needs-review ones, and that malformed output is caught rather than
   silently stored.
"""

from uuid import uuid4

import pytest

from backend.agents.playwright_agent import PlaywrightAgent
from backend.config.settings import LLMConfig
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus, TestCase
from backend.services.llm import LLMService
from backend.tests.conftest import StubLLMService

_TEST_LLM_CONFIG = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="fake-key-for-test")


def test_llm_service_generate_returns_mock_response_content():
    service = LLMService(config=_TEST_LLM_CONFIG)

    result = service.generate(
        system="irrelevant for this test",
        user="irrelevant - mock_response short-circuits the call",
        mock_response="plain text reply, no JSON involved",
    )

    assert result == "plain text reply, no JSON involved"


def _state_with_mixed_test_cases() -> WorkflowState:
    """One of each outcome, mirroring the doc's running example."""
    requirement = Requirement(title="Profile picture upload", description="Users can upload a JPG under 5MB")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="Valid Upload", description="d")
    approved = TestCase(scenario_id=scenario.id, title="Approved case", steps=["a"], expected_result="e",
                         evaluation_status=EvaluationStatus.APPROVED, confidence=0.95)
    needs_review = TestCase(scenario_id=scenario.id, title="Review case", steps=["a"], expected_result="e",
                             evaluation_status=EvaluationStatus.NEEDS_REVIEW, confidence=0.5)
    rejected = TestCase(scenario_id=scenario.id, title="Rejected case", steps=["a"], expected_result="e",
                         evaluation_status=EvaluationStatus.REJECTED, confidence=0.1)
    state = WorkflowState(requirement=requirement, generated_scenarios=[scenario],
                           generated_test_cases=[approved, needs_review, rejected])
    return state


def test_only_approved_test_cases_get_a_script(stub_llm_service):
    state = _state_with_mixed_test_cases()

    result = PlaywrightAgent(llm_service=stub_llm_service).run(state)

    by_title = {tc.title: tc for tc in result.generated_test_cases}
    assert by_title["Approved case"].playwright_script is not None
    assert by_title["Review case"].playwright_script is None  # never touched - agent doesn't know it exists
    assert by_title["Rejected case"].playwright_script is None


def test_strips_code_fences_from_output(stub_llm_service):
    state = _state_with_mixed_test_cases()

    result = PlaywrightAgent(llm_service=stub_llm_service).run(state)

    script = next(tc for tc in result.generated_test_cases if tc.title == "Approved case").playwright_script
    assert not script.startswith("```")
    assert "test(" in script
    assert "import" in script


def test_no_approved_test_cases_is_not_an_error():
    requirement = Requirement(title="x", description="y")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="s", description="d")
    rejected = TestCase(scenario_id=scenario.id, title="Rejected", steps=["a"], expected_result="e",
                         evaluation_status=EvaluationStatus.REJECTED, confidence=0.1)
    state = WorkflowState(requirement=requirement, generated_scenarios=[scenario], generated_test_cases=[rejected])

    result = PlaywrightAgent(llm_service=None).run(state)  # never called - nothing approved to convert

    assert "no approved test cases" in result.logs[-1]


def test_rejects_output_that_does_not_look_like_playwright_code():
    """If the model refuses or explains instead of writing code, that
    must fail loudly, not get stored as if it were a valid script."""
    state = _state_with_mixed_test_cases()
    bad_stub = StubLLMService(generate_response="I cannot generate this test because I don't have enough context.")

    with pytest.raises(ValueError, match="doesn't look like a Playwright test"):
        PlaywrightAgent(llm_service=bad_stub).run(state)


def test_requires_requirement():
    agent = PlaywrightAgent(llm_service=None)  # never called - guard fires first
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)