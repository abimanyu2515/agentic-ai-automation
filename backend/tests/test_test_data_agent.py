import pytest

from backend.agents.test_data_agent import TestDataAgent
from backend.models.requirement import Requirement
from backend.models.state import WorkflowState


def test_test_data_agent_requires_requirement():
    agent = TestDataAgent()
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)


def test_test_data_agent_generates_structured_report_and_exactly_20_rows():
    requirement = Requirement(
        title="User registration",
        description="Users register with full name, email, password, confirm password, and a required acceptance checkbox.",
        functional_requirements=[
            "Full name is required",
            "Email is required and must be valid",
            "Password is required and must meet complexity requirements",
            "Confirm password must match password",
            "Terms must be accepted",
        ],
        business_rules=[
            "Password minimum length is 8 characters",
            "Password must include a number and symbol",
        ],
        acceptance_criteria=[
            "A valid submission is accepted",
            "A mismatched confirm password is rejected",
            "A missing required field is rejected",
        ],
    )

    state = WorkflowState(requirement=requirement)
    result = TestDataAgent().run(state)

    assert result.test_data_report is not None
    assert "context_understood" in result.test_data_report
    assert "testable_combinations" in result.test_data_report
    assert "sample_dataset" in result.test_data_report
    assert "coverage_summary" in result.test_data_report

    rows = result.test_data_report["sample_dataset"]["rows"]
    header = result.test_data_report["sample_dataset"]["header"]
    coverage = result.test_data_report["coverage_summary"]
    assert len(rows) == 20
    assert len({row["test_data_id"] for row in rows}) == 20
    assert all(set(row) == set(header) for row in rows)
    assert len(result.generated_test_data) == 20
    assert coverage["number_of_data_rows"] == 20
    assert coverage["positive_cases"] == 8
    assert coverage["boundary_cases"] == 4
    assert coverage["negative_cases"] == 8
    assert len(result.test_data_combinations) >= 1
    assert result.test_data_report["sample_dataset"]["csv_text"].count("\n") >= 21

    first_row = rows[0]
    assert first_row["test_data_id"] == "TD-001"
    assert "expected_result" in first_row
    assert "test_type" in first_row


def test_test_data_agent_detects_human_readable_field_names():
    requirement = Requirement(
        title="User registration",
        description="Collect full name, email, password, confirm password, and accepted terms.",
    )

    fields = TestDataAgent()._detect_fields(requirement)

    assert fields == [
        "full_name",
        "email",
        "password",
        "confirm_password",
        "accept_terms",
    ]
