"""
Same two-layer approach as test_scenario_agent.py:

1. LLMService <-> litellm, using real mock_response - proves LLMService
   is genuinely generic (works for TestCaseListResponse, not just the
   schema ScenarioAgent happens to use).
2. TestCaseAgent <-> LLMService, using StubLLMService (conftest.py) -
   verifies the agent's own logic: scenario-number correlation, priority
   inheritance, and the failure mode unique to this agent (an LLM
   response with scenario numbers that don't match what was sent).

Correlation is number-based, not name-based - see test_case_agent.py's
docstring for why: this used to echo scenario_name back, and it broke in
a real live run when a model paraphrased a name instead of copying it
exactly. Numbers don't get paraphrased the same way.
"""

import json
from threading import Lock
import time

import pytest

from backend.agents.test_case_agent import TestCaseAgent, TestCaseListResponse
from backend.config.settings import LLMConfig
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.services.llm import LLMService, LLMServiceError

_TEST_LLM_CONFIG = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="fake-key-for-test")


def test_llm_service_structured_generate_parses_test_case_mock_response():
    """Same LLMService, different schema - confirms it's genuinely
    generic rather than accidentally shaped around ScenarioAgent."""
    service = LLMService(config=_TEST_LLM_CONFIG)

    result = service.structured_generate(
        user="irrelevant - mock_response short-circuits the call",
        response_model=TestCaseListResponse,
        mock_response=(
            '{"test_cases": [{"scenario_number": 1, "title": "Verify login", '
            '"preconditions": ["App running"], "steps": ["Go to login", "Enter creds"], '
            '"expected_result": "User is logged in"}]}'
        ),
    )

    assert len(result.test_cases) == 1
    assert result.test_cases[0].scenario_number == 1


def test_llm_service_parses_stringified_test_case_entries():
    service = LLMService(config=_TEST_LLM_CONFIG)
    second_case = {
        "scenario_number": 2,
        "title": "Verify registration with valid data",
        "preconditions": ["Registration page is open"],
        "steps": ["Submit valid registration details"],
        "expected_result": "The registration succeeds",
        "test_data": {"email": "user@example.com"},
    }

    result = service.structured_generate(
        user="irrelevant - mock_response short-circuits the call",
        response_model=TestCaseListResponse,
        mock_response=json.dumps(
            {
                "test_cases": [
                    {
                        "scenario_number": 1,
                        "title": "Verify login",
                        "preconditions": ["App running"],
                        "steps": ["Go to login", "Enter credentials"],
                        "expected_result": "User is logged in",
                        "test_data": {},
                    },
                    json.dumps(json.dumps(second_case)),
                ]
            }
        ),
    )

    assert [case.scenario_number for case in result.test_cases] == [1, 2]
    assert result.test_cases[1].title == second_case["title"]
    assert result.test_cases[1].test_data == second_case["test_data"]


def test_test_case_agent_batches_large_scenario_sets():
    class BatchLLMService:
        def __init__(self):
            self.calls = []

        def structured_generate(self, *, user, response_model, **kwargs):
            self.calls.append((user, kwargs))
            scenario_count = min(5, 7 - (len(self.calls) - 1) * 5)
            return response_model(
                test_cases=[
                    {
                        "scenario_number": number,
                        "title": f"Generated case {number}",
                        "steps": ["Complete the scenario"],
                        "expected_result": "The scenario completes successfully",
                    }
                    for number in range(1, scenario_count + 1)
                ]
            )

    llm_service = BatchLLMService()
    state = _state_with_scenarios()
    state.generated_scenarios.extend(
        Scenario(
            requirement_id=state.requirement.id,
            scenario_name=f"Additional scenario {number}",
            description="Additional scenario description",
            priority="medium",
        )
        for number in range(4, 8)
    )

    result = TestCaseAgent(llm_service=llm_service).run(state)

    assert len(llm_service.calls) == 2
    assert [case.scenario_id for case in result.generated_test_cases] == [
        scenario.id for scenario in state.generated_scenarios
    ]
    assert all(kwargs == {"timeout": 120, "max_retries": 2} for _, kwargs in llm_service.calls)


def test_test_case_agent_respects_configured_worker_limit():
    class ConcurrentBatchLLMService:
        def __init__(self):
            self.lock = Lock()
            self.active_calls = 0
            self.max_active_calls = 0

        def structured_generate(self, *, user, response_model, **kwargs):
            with self.lock:
                self.active_calls += 1
                self.max_active_calls = max(self.max_active_calls, self.active_calls)
            time.sleep(0.02)
            count = user.count("(priority:")
            result = response_model(
                test_cases=[
                    {
                        "scenario_number": number,
                        "title": f"Generated case {number}",
                        "steps": ["Complete the scenario"],
                        "expected_result": "The scenario completes successfully",
                    }
                    for number in range(1, count + 1)
                ]
            )
            with self.lock:
                self.active_calls -= 1
            return result

    state = _state_with_scenarios()
    state.generated_scenarios.extend(
        Scenario(
            requirement_id=state.requirement.id,
            scenario_name=f"Additional scenario {number}",
            description="Additional scenario description",
            priority="medium",
        )
        for number in range(4, 8)
    )
    llm_service = ConcurrentBatchLLMService()

    result = TestCaseAgent(llm_service=llm_service, max_workers=2).run(state)

    assert llm_service.max_active_calls == 2
    assert [case.scenario_id for case in result.generated_test_cases] == [
        scenario.id for scenario in state.generated_scenarios
    ]


def _state_with_scenarios() -> WorkflowState:
    """Matches canned_scenario_batch in conftest.py exactly - the shared
    stub_llm_service fixture's canned test cases are keyed to these three
    scenarios, in this order, since correlation is now by position."""
    requirement = Requirement(title="Login flow", description="User can log in with valid credentials")
    state = WorkflowState(requirement=requirement)
    state.generated_scenarios = [
        Scenario(requirement_id=requirement.id, scenario_name="Valid Login", description="desc", priority="high"),
        Scenario(requirement_id=requirement.id, scenario_name="Invalid Login", description="desc", priority="medium"),
        Scenario(requirement_id=requirement.id, scenario_name="Empty Password", description="desc", priority="low"),
    ]
    return state


def test_test_case_agent_correlates_by_scenario_number_and_inherits_priority(stub_llm_service):
    agent = TestCaseAgent(llm_service=stub_llm_service)
    state = _state_with_scenarios()

    result = agent.run(state)

    assert len(result.generated_test_cases) == 3
    tc = result.generated_test_cases[0]
    matching_scenario = next(s for s in state.generated_scenarios if s.id == tc.scenario_id)
    assert tc.priority == matching_scenario.priority  # inherited, not re-invented by the LLM
    assert "TestCase Agent generated 3 test cases" in result.logs[-1]


def test_test_case_agent_rejects_mismatched_scenario_numbers():
    """The failure mode that actually happened in a real live run: the
    LLM's response doesn't correlate with what was sent (there, a
    paraphrased name; here, a number outside the valid range). Must fail
    loudly, and as LLMServiceError specifically - live_check.py catches
    that type to show a clean message instead of a raw traceback."""
    from backend.tests.conftest import StubLLMService

    bad_batch = TestCaseListResponse.model_validate(
        {
            "test_cases": [
                {"scenario_number": 99, "title": "x", "preconditions": [], "steps": ["step"], "expected_result": "y"}
            ]
        }
    )
    stub = StubLLMService({TestCaseListResponse: bad_batch})
    agent = TestCaseAgent(llm_service=stub)
    state = _state_with_scenarios()

    with pytest.raises(LLMServiceError, match="expected exactly"):
        agent.run(state)


def test_test_case_agent_requires_requirement():
    agent = TestCaseAgent(llm_service=None)  # never called - the guard fires first
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)


def test_test_case_agent_requires_generated_scenarios():
    agent = TestCaseAgent(llm_service=None)  # never called - the guard fires first
    requirement = Requirement(title="x", description="y")
    state = WorkflowState(requirement=requirement)  # no scenarios added

    with pytest.raises(ValueError):
        agent.run(state)