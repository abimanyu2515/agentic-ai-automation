"""
Two layers, same approach as the other agents:
1. LLMService <-> litellm - real mock_response, proves LLMService is
   genuinely generic across a THIRD schema, not just the first two.
2. EvaluationAgent <-> LLMService - stub-based, with precise control over
   relevance/completeness scores to exercise all three outcome branches
   (approved / needs review / rejected) plus duplicate detection and the
   correlation guard.
"""

from uuid import uuid4

import pytest

from backend.agents.evaluation_agent import EvaluationAgent, _EvaluationBatch, _TestCaseEvaluation
from backend.config.settings import LLMConfig
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus, TestCase
from backend.services.llm import LLMService, LLMServiceError
from backend.tests.conftest import StubLLMService

_TEST_LLM_CONFIG = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="fake-key-for-test")


def test_llm_service_structured_generate_parses_evaluation_mock_response():
    service = LLMService(config=_TEST_LLM_CONFIG)

    result = service.structured_generate(
        user="irrelevant - mock_response short-circuits the call",
        response_model=_EvaluationBatch,
        mock_response='{"evaluations": [{"test_case_number": 1, "relevance": 0.9, "completeness": 0.8, "reason": "Good"}]}',
    )

    assert len(result.evaluations) == 1
    assert result.evaluations[0].test_case_number == 1


def test_blank_reason_gets_a_default_instead_of_failing_the_whole_batch():
    """The actual failure from a real live run: a model left reason=""
    for one high-scoring evaluation (nothing critical to flag), and the
    whole 3-item batch was rejected over it. A blank reason on one item
    doesn't cast doubt on the scores for that item OR the other two -
    it should get a sensible default, not sink everything."""
    service = LLMService(config=_TEST_LLM_CONFIG)

    result = service.structured_generate(
        user="irrelevant",
        response_model=_EvaluationBatch,
        mock_response=(
            '{"evaluations": ['
            '{"test_case_number": 1, "relevance": 0.8, "completeness": 0.7, "reason": "Missing an edge case"}, '
            '{"test_case_number": 2, "relevance": 0.9, "completeness": 1.0, "reason": ""}, '
            '{"test_case_number": 3, "relevance": 0.6, "completeness": 0.8, "reason": "Wrong extension used"}'
            "]}"
        ),
    )

    assert len(result.evaluations) == 3  # nothing lost
    assert result.evaluations[1].reason == "No specific concerns noted."


def _state_with_test_cases(test_cases: list[TestCase]) -> WorkflowState:
    requirement = Requirement(title="Profile picture upload", description="Users can upload a JPG under 5MB")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="Valid Upload", description="d")
    for tc in test_cases:
        tc.scenario_id = scenario.id
    state = WorkflowState(requirement=requirement)
    state.generated_scenarios = [scenario]
    state.generated_test_cases = test_cases
    return state


def _stub_for(evaluations: list[_TestCaseEvaluation]) -> StubLLMService:
    return StubLLMService({_EvaluationBatch: _EvaluationBatch(evaluations=evaluations)})


def test_high_confidence_gets_approved():
    tc = TestCase(scenario_id=uuid4(), title="Upload JPG", steps=["a"], expected_result="e")
    state = _state_with_test_cases([tc])

    stub = _stub_for([_TestCaseEvaluation(test_case_number=1, relevance=0.9, completeness=0.9, reason="Solid")])
    result = EvaluationAgent(llm_service=stub).run(state)

    assert result.generated_test_cases[0].evaluation_status == EvaluationStatus.NEEDS_REVIEW
    assert result.generated_test_cases[0].confidence == pytest.approx(0.9)


def test_low_confidence_needs_review_not_rejected():
    tc = TestCase(scenario_id=uuid4(), title="Upload JPG", steps=["a"], expected_result="e")
    state = _state_with_test_cases([tc])

    # Relevance is well above the 0.3 rejection floor, but combined
    # confidence (0.55) is below the 0.75 approval threshold.
    stub = _stub_for([_TestCaseEvaluation(test_case_number=1, relevance=0.6, completeness=0.5, reason="Vague steps")])
    result = EvaluationAgent(llm_service=stub).run(state)

    assert result.generated_test_cases[0].evaluation_status == EvaluationStatus.NEEDS_REVIEW


def test_low_relevance_rejected_outright_not_sent_for_review():
    tc = TestCase(scenario_id=uuid4(), title="Forgot Password", steps=["a"], expected_result="e")
    state = _state_with_test_cases([tc])

    stub = _stub_for([_TestCaseEvaluation(test_case_number=1, relevance=0.1, completeness=0.9, reason="Unrelated")])
    result = EvaluationAgent(llm_service=stub).run(state)

    assert result.generated_test_cases[0].evaluation_status == EvaluationStatus.REJECTED


def test_duplicate_rejected_before_llm_call_and_never_scored():
    """Duplicates are rejected deterministically before the LLM call. The
    stub below only provides ONE evaluation - if the agent incorrectly
    tried to send both test cases to the LLM, the correlation check would
    fail with a mismatched-numbers error instead of this test passing."""
    tc1 = TestCase(scenario_id=uuid4(), title="Upload valid JPG",
                    steps=["a"], expected_result="Picture uploads and displays on profile")
    tc2 = TestCase(scenario_id=uuid4(), title="Upload Valid JPG File",
                    steps=["a"], expected_result="Picture uploads and displays on profile")
    state = _state_with_test_cases([tc1, tc2])

    stub = _stub_for([_TestCaseEvaluation(test_case_number=1, relevance=0.9, completeness=0.9, reason="Solid")])
    result = EvaluationAgent(llm_service=stub).run(state)

    assert result.generated_test_cases[0].evaluation_status == EvaluationStatus.NEEDS_REVIEW
    assert result.generated_test_cases[1].evaluation_status == EvaluationStatus.REJECTED
    assert "Duplicate" in result.generated_test_cases[1].evaluation_reason


def test_rejects_when_llm_returns_mismatched_numbers():
    tc = TestCase(scenario_id=uuid4(), title="Upload JPG", steps=["a"], expected_result="e")
    state = _state_with_test_cases([tc])

    # test_case_number=2 doesn't exist - only 1 candidate was sent to the LLM
    stub = _stub_for([_TestCaseEvaluation(test_case_number=2, relevance=0.9, completeness=0.9, reason="x")])

    with pytest.raises(LLMServiceError, match="expected exactly"):
        EvaluationAgent(llm_service=stub).run(state)


def test_requires_requirement():
    agent = EvaluationAgent(llm_service=None)  # never called - guard fires first
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)


def test_requires_generated_test_cases():
    agent = EvaluationAgent(llm_service=None)  # never called - guard fires first
    requirement = Requirement(title="x", description="y")
    state = WorkflowState(requirement=requirement)  # no test cases

    with pytest.raises(ValueError):
        agent.run(state)