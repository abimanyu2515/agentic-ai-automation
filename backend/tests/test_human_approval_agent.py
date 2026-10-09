"""
HumanApprovalAgent needs no LLM, so unlike every other agent test file,
there's no stub and no mock_response anywhere here - just direct calls.
"""

import pytest

from backend.agents.human_approval_agent import HumanApprovalAgent
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus, TestCase


def _state_with_test_cases() -> tuple[WorkflowState, TestCase, TestCase, TestCase]:
    """Mirrors the doc's exact example: TC1 auto-approved, TC2 needs
    review, TC3 rejected."""
    requirement = Requirement(title="Profile picture upload", description="Users can upload a JPG under 5MB")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="Valid Upload", description="d")
    tc1 = TestCase(scenario_id=scenario.id, title="TC1", steps=["a"], expected_result="e",
                    evaluation_status=EvaluationStatus.APPROVED, confidence=0.95)
    tc2 = TestCase(scenario_id=scenario.id, title="TC2", steps=["a"], expected_result="e",
                    evaluation_status=EvaluationStatus.NEEDS_REVIEW, confidence=0.42)
    tc3 = TestCase(scenario_id=scenario.id, title="TC3", steps=["a"], expected_result="e",
                    evaluation_status=EvaluationStatus.REJECTED, confidence=0.10)
    state = WorkflowState(requirement=requirement, generated_scenarios=[scenario],
                           generated_test_cases=[tc1, tc2, tc3])
    return state, tc1, tc2, tc3


def test_approving_needs_review_item_makes_it_available_to_playwright():
    state, tc1, tc2, tc3 = _state_with_test_cases()
    state.request_test_case_approval(tc2.id)

    result = HumanApprovalAgent().run(state)

    approved_titles = {tc.title for tc in result.approved_test_cases()}
    assert approved_titles == {"TC1", "TC2"}  # TC3 never appears, matches the doc exactly


def test_no_pending_approvals_is_not_an_error():
    state, tc1, tc2, tc3 = _state_with_test_cases()  # nothing requested

    result = HumanApprovalAgent().run(state)

    assert result.approved_test_cases() == [tc1]  # unchanged - only the auto-approved one
    assert "no pending human decisions" in result.logs[-1]


def test_approving_already_approved_item_is_a_harmless_noop():
    state, tc1, tc2, tc3 = _state_with_test_cases()
    state.request_test_case_approval(tc1.id)  # already APPROVED by evaluation

    result = HumanApprovalAgent().run(state)

    assert [tc.title for tc in result.approved_test_cases()] == ["TC1"]  # still just one, no duplicate


def test_cannot_approve_a_rejected_test_case():
    state, tc1, tc2, tc3 = _state_with_test_cases()
    state.request_test_case_approval(tc3.id)  # rejected as duplicate/irrelevant

    with pytest.raises(ValueError, match="cannot approve"):
        HumanApprovalAgent().run(state)


def test_request_approval_rejects_unknown_test_case_id():
    from uuid import uuid4
    state, *_ = _state_with_test_cases()

    with pytest.raises(ValueError, match="not in generated_test_cases"):
        state.request_test_case_approval(uuid4())


def test_requires_requirement():
    agent = HumanApprovalAgent()
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)