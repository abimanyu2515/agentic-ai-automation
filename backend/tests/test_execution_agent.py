"""
ExecutionAgent needs no LLM, so like HumanApprovalAgent's tests, there's
no stub_llm_service here - just FakePlaywrightRunner (conftest.py),
which never touches subprocess/npx. The real PlaywrightRunner <-> npx
integration is verified separately, directly against a real local
Playwright install (see playwright_runner.py's docstring) - not
re-verified here, since that's a different layer with a different
failure mode (a real CLI crashing vs this agent's own orchestration logic).
"""

from backend.agents.execution_agent import ExecutionAgent
from backend.models.execution_result import ExecutionStatus
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus, TestCase, TestCaseStatus
from backend.services.playwright_runner import PlaywrightRunnerError, PlaywrightRunResult
from backend.tests.conftest import FakePlaywrightRunner

import pytest


def _state_with_approved_test_case(playwright_script: str | None = "some script") -> tuple[WorkflowState, TestCase]:
    requirement = Requirement(title="x", description="y")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="s", description="d")
    tc = TestCase(
        scenario_id=scenario.id, title="Approved case", steps=["a"], expected_result="e",
        evaluation_status=EvaluationStatus.APPROVED, confidence=0.9, playwright_script=playwright_script,
    )
    state = WorkflowState(requirement=requirement, generated_scenarios=[scenario], generated_test_cases=[tc])
    return state, tc


def test_passing_execution_updates_test_case_status():
    state, tc = _state_with_approved_test_case()
    runner = FakePlaywrightRunner(default_result=PlaywrightRunResult(status="passed", duration_seconds=1.2))

    result_state = ExecutionAgent(runner=runner).run(state)

    assert tc.status == TestCaseStatus.PASSED
    assert len(result_state.execution_results) == 1
    assert result_state.execution_results[0].status == ExecutionStatus.PASSED
    assert result_state.execution_results[0].test_case_id == tc.id
    assert result_state.execution_results[0].duration_seconds == 1.2


def test_failing_execution_records_error_message():
    state, tc = _state_with_approved_test_case()
    runner = FakePlaywrightRunner(
        default_result=PlaywrightRunResult(status="failed", duration_seconds=0.8, error_message="assertion failed")
    )

    result_state = ExecutionAgent(runner=runner).run(state)

    assert tc.status == TestCaseStatus.FAILED
    assert result_state.execution_results[0].error_message == "assertion failed"


def test_error_status_maps_to_blocked_not_failed():
    """A syntax error in the generated script is a tooling problem, not
    a finding about the app under test - status=error should map to
    BLOCKED, distinct from an actual test failure."""
    state, tc = _state_with_approved_test_case()
    runner = FakePlaywrightRunner(default_result=PlaywrightRunResult(status="error", error_message="syntax error"))

    result_state = ExecutionAgent(runner=runner).run(state)

    assert tc.status == TestCaseStatus.BLOCKED
    assert result_state.execution_results[0].status == ExecutionStatus.ERROR


def test_skips_approved_test_case_with_no_script():
    """Approved but no script shouldn't happen in normal pipeline order,
    but it's not an error here - just nothing to execute for that one."""
    state, tc = _state_with_approved_test_case(playwright_script=None)

    result_state = ExecutionAgent(runner=None).run(state)  # never called - nothing runnable

    assert len(result_state.execution_results) == 0
    assert "no approved test cases with a generated script" in result_state.logs[-1]


def test_rejected_test_case_never_gets_executed():
    requirement = Requirement(title="x", description="y")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="s", description="d")
    rejected = TestCase(scenario_id=scenario.id, title="Rejected", steps=["a"], expected_result="e",
                         evaluation_status=EvaluationStatus.REJECTED, playwright_script="should never run this")
    state = WorkflowState(requirement=requirement, generated_scenarios=[scenario], generated_test_cases=[rejected])

    result_state = ExecutionAgent(runner=None).run(state)  # never called

    assert len(result_state.execution_results) == 0
    assert rejected.status == TestCaseStatus.PENDING  # untouched


def test_requires_requirement():
    agent = ExecutionAgent(runner=None)  # never called - guard fires first
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)


def test_one_test_case_erroring_does_not_lose_others_in_the_batch():
    """The actual bug this round fixed: a real Windows run hit an
    unrecoverable timeout on one test case, and an unhandled exception
    crashed the whole graph, losing results for every test case,
    including ones that hadn't run yet. This proves that can't happen
    again - a PlaywrightRunnerError for one test case must not cost the
    others their results."""
    requirement = Requirement(title="x", description="y")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="s", description="d")
    tc1 = TestCase(scenario_id=scenario.id, title="First", steps=["a"], expected_result="e",
                    evaluation_status=EvaluationStatus.APPROVED, playwright_script="script1")
    tc2 = TestCase(scenario_id=scenario.id, title="Second", steps=["a"], expected_result="e",
                    evaluation_status=EvaluationStatus.APPROVED, playwright_script="script2")
    state = WorkflowState(requirement=requirement, generated_scenarios=[scenario], generated_test_cases=[tc1, tc2])

    class FlakyRunner:
        def run(self, script, run_id):
            if run_id == str(tc1.id):
                raise PlaywrightRunnerError("npx vanished mid-batch")
            return PlaywrightRunResult(status="passed", duration_seconds=1.0)

    result_state = ExecutionAgent(runner=FlakyRunner()).run(state)

    assert len(result_state.execution_results) == 2  # neither lost
    tc1_result = next(r for r in result_state.execution_results if r.test_case_id == tc1.id)
    tc2_result = next(r for r in result_state.execution_results if r.test_case_id == tc2.id)
    assert tc1_result.status == ExecutionStatus.ERROR
    assert tc2_result.status == ExecutionStatus.PASSED  # still ran despite tc1's failure
    assert tc1.status == TestCaseStatus.BLOCKED