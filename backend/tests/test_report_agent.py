"""
ReportAgent needs no LLM and no subprocess - unlike every other test
file, there's no stub, no fake runner, nothing to mock. Just plain state
in, plain report out.
"""

import pytest

from backend.agents.report_agent import ReportAgent
from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus, TestCase, TestCaseStatus


def _built_state():
    requirement = Requirement(title="Profile Picture Upload", description="d")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="s", description="d")

    passed_tc = TestCase(scenario_id=scenario.id, title="Upload JPG", steps=["a"], expected_result="e",
                          evaluation_status=EvaluationStatus.APPROVED, status=TestCaseStatus.PASSED)
    failed_tc = TestCase(scenario_id=scenario.id, title="Upload oversized PNG", steps=["a"], expected_result="e",
                          evaluation_status=EvaluationStatus.APPROVED, status=TestCaseStatus.FAILED)
    rejected_tc = TestCase(scenario_id=scenario.id, title="Duplicate test", steps=["a"], expected_result="e",
                            evaluation_status=EvaluationStatus.REJECTED)  # never executed

    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[passed_tc, failed_tc, rejected_tc],
    )
    state.execution_results = [
        ExecutionResult(test_case_id=passed_tc.id, status=ExecutionStatus.PASSED, duration_seconds=2.1),
        ExecutionResult(test_case_id=failed_tc.id, status=ExecutionStatus.FAILED, duration_seconds=1.5,
                         error_message="Expected validation message not found."),
    ]
    return state, passed_tc, failed_tc, rejected_tc


def test_counts_are_correct():
    state, passed_tc, failed_tc, rejected_tc = _built_state()

    report = ReportAgent().run(state).execution_report

    assert report.scenario_count == 1
    assert report.test_case_count == 3
    assert report.approved_count == 2  # passed_tc and failed_tc were both evaluation-approved
    assert report.rejected_count == 1
    assert report.needs_review_count == 0
    assert report.executed_count == 2  # rejected_tc never ran
    assert report.passed_count == 1
    assert report.failed_count == 1
    assert report.blocked_count == 0


def test_pass_rate_and_total_duration():
    state, *_ = _built_state()

    report = ReportAgent().run(state).execution_report

    assert report.pass_rate == pytest.approx(0.5)
    assert report.total_execution_seconds == pytest.approx(3.6)


def test_failed_entry_includes_the_real_error_message():
    state, passed_tc, failed_tc, rejected_tc = _built_state()

    report = ReportAgent().run(state).execution_report

    assert len(report.failed_tests) == 1
    entry = report.failed_tests[0]
    assert entry.title == "Upload oversized PNG"
    assert entry.reason == "Expected validation message not found."
    assert entry.duration_seconds == pytest.approx(1.5)


def test_pass_rate_is_zero_not_a_crash_when_nothing_executed():
    requirement = Requirement(title="x", description="y")
    state = WorkflowState(requirement=requirement)  # completely empty otherwise

    report = ReportAgent().run(state).execution_report

    assert report.executed_count == 0
    assert report.pass_rate == 0.0  # not a ZeroDivisionError
    assert report.scenario_count == 0
    assert report.test_case_count == 0


def test_requires_requirement():
    agent = ReportAgent()
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)