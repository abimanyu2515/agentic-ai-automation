import pytest

from backend.agents.test_data_agent import (
    TestCaseDataBatchResponse,
    TestCaseDataResponse,
    TestDataAgent,
)
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase


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


def test_test_data_agent_retries_duplicate_llm_batches_for_unsupported_rule():
    requirement = Requirement(
        title="User registration",
        description="Users register with a contact phone number.",
        business_rules=[
            "Phone numbers must use a supported international format."
        ],
    )
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Reject invalid phone",
        description="Reject a phone number that does not use a supported format.",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Invalid phone number",
        steps=["Enter an unsupported phone number", "Submit the form"],
        expected_result="Phone validation error is shown",
    )
    def batch(start):
        return TestCaseDataBatchResponse(
            datasets=[
                TestCaseDataResponse(
                    test_data={
                        "phone": (
                            "invalid-phone" if start == 0 and index == 0
                            else f"invalid-{start + index}"
                        ),
                    },
                    targeted_validation_rule="Phone number must use a supported international format.",
                    test_type="invalid",
                )
                for index in range(20)
            ]
        )

    responses = [batch(0), batch(100)]

    class StubLLMService:
        def __init__(self):
            self.prompts = []
            self.options = []

        def structured_generate(self, *, user, response_model, **kwargs):
            assert response_model is TestCaseDataBatchResponse
            self.prompts.append(user)
            self.options.append(kwargs)
            return responses.pop(0)

    llm_service = StubLLMService()
    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
        testing_context={
            "reserved_test_data": [
                {"phone": "invalid-phone"}
            ]
        },
    )

    result = TestDataAgent(llm_service=llm_service).run(state)

    assert len(llm_service.prompts) == 2
    assert llm_service.options == [
        {"max_retries": 1, "timeout": 240},
        {"max_retries": 1, "timeout": 240},
    ]
    assert "unsupported phone number" in llm_service.prompts[0]
    assert len(result.generated_test_data) == 20
    assert len(result.test_data_report["sample_dataset"]["rows"]) == 20
    assert len(result.generated_test_cases[0].test_data_sets) == 20
    assert len({
        TestDataAgent._dataset_fingerprint(dataset)
        for dataset in result.generated_test_cases[0].test_data_sets
    }) == 20
    # The reserved "invalid-phone" is skipped, the other 19 from the first
    # reply are kept, and the second reply supplies the 20th.
    phones = [row["phone"] for row in result.generated_test_data]
    assert phones == [f"invalid-{i}" for i in range(1, 20)] + ["invalid-100"]
    assert result.test_data_report["context_understood"]["expected_result"] == test_case.expected_result
    assert result.test_data_report["context_understood"]["unique_for_test_case"] is True


def test_test_data_agent_generates_numeric_password_data_from_existing_test_data():
    requirement = Requirement(
        title="User registration",
        description="Register with an email address and password.",
        business_rules=[
            "Password must include at least one letter and one symbol."
        ],
    )
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Reject numeric-only password",
        description="Reject a password containing only numeric characters.",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Test data TD-016: invalid",
        steps=["Submit a password containing only digits"],
        expected_result="Password is rejected",
        test_data={
            "test_data_id": "TD-016",
            "email": "user@example.test",
            "password": "12345678",
            "targeted_validation_rule": "Password contains only numeric characters.",
            "expected_result": "Password is rejected",
            "test_type": "invalid",
        },
    )

    class UnusedLLMService:
        def structured_generate(self, **kwargs):
            raise AssertionError("Recognized numeric-password data should be generated locally")

    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
        testing_context={
            "reserved_test_data": [
                {"email": "user@example.test", "password": f"{index:08d}"}
                for index in range(1, 21)
            ]
        },
    )

    result = TestDataAgent(llm_service=UnusedLLMService()).run(state)

    datasets = test_case.test_data_sets
    assert len(datasets) == 20
    assert len({TestDataAgent._dataset_fingerprint(dataset) for dataset in datasets}) == 20
    assert all(dataset["password"].isdigit() for dataset in datasets)
    assert all(dataset["email"] == "user@example.test" for dataset in datasets)
    assert all(
        set(dataset) == {"email", "password"}
        for dataset in datasets
    )
    assert {
        TestDataAgent._dataset_fingerprint(dataset) for dataset in datasets
    }.isdisjoint({
        TestDataAgent._dataset_fingerprint(dataset)
        for dataset in state.testing_context["reserved_test_data"]
    })
    assert result.test_data_report["context_understood"]["number_of_datasets"] == 20


@pytest.mark.parametrize(
    ("title", "steps", "expected_result", "changed_field", "target_rule"),
    [
        (
            "Login rejects an invalid username",
            ["Enter an unknown username", "Enter a valid password", "Submit"],
            "Username is rejected",
            "username",
            "Unknown usernames must be rejected.",
        ),
        (
            "Login requires an empty username",
            ["Leave username empty", "Enter a password", "Submit"],
            "Username required message is displayed",
            "username",
            "Username is required and cannot be empty.",
        ),
        (
            "Login rejects an invalid password",
            ["Enter a valid username", "Enter an incorrect password", "Submit"],
            "Password is rejected",
            "password",
            "Incorrect passwords must be rejected.",
        ),
        (
            "Login requires an empty password",
            ["Enter a username", "Leave password empty", "Submit"],
            "Password required message is displayed",
            "password",
            "Password is required and cannot be empty.",
        ),
    ],
)
def test_login_case_generation_targets_only_the_selected_validation(
    title,
    steps,
    expected_result,
    changed_field,
    target_rule,
):
    requirement = Requirement(
        title="Swag Labs login",
        description="Validate the login form for the selected case.",
    )
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name=title,
        description="Use the configured Swag Labs login form.",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title=title,
        steps=steps,
        expected_result=expected_result,
        test_data={"username": "standard_user", "password": "vault-managed"},
    )

    class UnusedLLMService:
        def structured_generate(self, **kwargs):
            raise AssertionError("Recognized login validation cases should use local data")

    result = TestDataAgent(llm_service=UnusedLLMService()).run(
        WorkflowState(
            requirement=requirement,
            generated_scenarios=[scenario],
            generated_test_cases=[test_case],
        )
    )

    datasets = test_case.test_data_sets
    assert len(datasets) == 20
    assert len({TestDataAgent._dataset_fingerprint(dataset) for dataset in datasets}) == 20
    assert all(
        dataset[changed_field] == ""
        for dataset in datasets
    ) if "empty" in title else len({
        dataset[changed_field] for dataset in datasets
    }) == 20
    if "empty" in title:
        varied_field = "password" if changed_field == "username" else "username"
        assert len({dataset[varied_field] for dataset in datasets}) == 20
    else:
        unchanged_field = "password" if changed_field == "username" else "username"
        safe_value = (
            "synthetic-password"
            if unchanged_field == "password"
            else "synthetic-user"
        )
        assert all(
            dataset[unchanged_field] == safe_value
            for dataset in datasets
        )
    assert all(
        row["targeted_validation_rule"] == target_rule
        for row in result.generated_test_data
    )


def test_generic_invalid_login_does_not_assume_numeric_password_rule():
    requirement = Requirement(title="Login", description="Validate user login.")
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Reject invalid login",
        description="Reject an invalid login attempt.",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Invalid login",
        steps=["Enter login details", "Submit"],
        expected_result="Login is rejected",
        test_data={"username": "unknown-user", "password": "incorrect-password"},
    )

    class StubLLMService:
        def structured_generate(self, **kwargs):
            return TestCaseDataBatchResponse(
                datasets=[
                    TestCaseDataResponse(
                        test_data={
                            "username": f"unknown-user-{index:02d}",
                            "password": f"incorrect-password-{index:02d}",
                        },
                        targeted_validation_rule="Invalid login credentials are rejected.",
                        test_type="invalid",
                    )
                    for index in range(1, 21)
                ]
            )

    result = TestDataAgent(llm_service=StubLLMService()).run(
        WorkflowState(
            requirement=requirement,
            generated_scenarios=[scenario],
            generated_test_cases=[test_case],
        )
    )

    assert len(result.generated_test_data) == 20
    assert all(
        dataset["targeted_validation_rule"]
        == "Invalid login credentials are rejected."
        for dataset in result.generated_test_data
    )


def test_test_data_agent_reads_legacy_row_from_scenario_for_generic_invalid_case():
    requirement = Requirement(
        title="User registration",
        description="Register with a name, email, password, and terms acceptance.",
    )
    legacy_row = {
        "test_data_id": "TD-016",
        "full_name": "Synthetic User",
        "email": "user@example.test",
        "password": "ValidPass1!",
        "confirm_password": "ValidPass1!",
        "accept_terms": "true",
        "expected_result": "rejected_validation_error",
        "test_type": "invalid",
    }
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Test data TD-016: invalid",
        description=(
            f"Exercise the requirement with the generated input data TD-016: "
            f"{legacy_row}. Expected result: rejected_validation_error."
        ),
        path_type="invalid",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Test data TD-016: invalid",
        steps=[scenario.description],
        expected_result="Registration is rejected with a validation error.",
    )

    class UnusedLLMService:
        def structured_generate(self, **kwargs):
            raise AssertionError("The legacy invalid password case should use local data")

    result = TestDataAgent(llm_service=UnusedLLMService()).run(
        WorkflowState(
            requirement=requirement,
            generated_scenarios=[scenario],
            generated_test_cases=[test_case],
        )
    )

    assert len(result.generated_test_data) == 20
    assert len(test_case.test_data_sets) == 20
    assert all(dataset["password"].isdigit() for dataset in test_case.test_data_sets)
    assert all(
        dataset["confirm_password"] == dataset["password"]
        for dataset in test_case.test_data_sets
    )
    assert all(
        dataset["email"] == legacy_row["email"]
        and dataset["full_name"] == legacy_row["full_name"]
        and dataset["accept_terms"] == "true"
        for dataset in test_case.test_data_sets
    )
    assert all(
        set(dataset) == {"full_name", "email", "password", "confirm_password", "accept_terms"}
        for dataset in test_case.test_data_sets
    )


def test_test_data_agent_fails_when_llm_cannot_generate_unique_unsupported_data():
    requirement = Requirement(title="User registration", description="Register a user.")
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Invalid phone",
        description="Reject unsupported phone numbers.",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Reject unsupported phone",
        steps=["Enter an unsupported phone number", "Submit"],
        expected_result="Phone validation error is shown",
    )
    duplicate = TestCaseDataBatchResponse(
        datasets=[
            TestCaseDataResponse(
                test_data={"phone": "invalid-phone"},
                targeted_validation_rule="Phone number must use a supported format.",
                test_type="invalid",
            )
            for _ in range(20)
        ]
    )

    class StubLLMService:
        calls = 0

        def structured_generate(self, **kwargs):
            self.calls += 1
            return duplicate

    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
        testing_context={"reserved_test_data": [{"phone": "invalid-phone"}]},
    )

    with pytest.raises(ValueError, match="could not generate 20 unique datasets"):
        TestDataAgent(llm_service=StubLLMService()).run(state)


def _phone_state():
    requirement = Requirement(title="User registration", description="Register a user.")
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Invalid phone",
        description="Reject unsupported phone numbers.",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Reject unsupported phone",
        steps=["Enter an unsupported phone number", "Submit"],
        expected_result="Phone validation error is shown",
    )
    return WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
    )


def _phone_dataset(value):
    return {
        "test_data": {"phone": value},
        "targeted_validation_rule": "Phone number must use a supported format.",
        "test_type": "invalid",
    }


def test_batch_response_drops_malformed_items_and_accepts_more_than_20():
    """The live failure: 44 entries, some of them bare numbers, rejected
    outright for exceeding 20 even though 20+ usable datasets were present."""
    raw = [32995941, *[_phone_dataset(f"invalid-{i}") for i in range(43)]]

    batch = TestCaseDataBatchResponse.model_validate({"datasets": raw})

    assert len(batch.datasets) == 43


def test_test_data_agent_keeps_20_unique_datasets_from_oversized_response():
    raw = [
        32995941,
        *[_phone_dataset(f"invalid-{i}") for i in range(30)],
        _phone_dataset("invalid-0"),
        {"test_data": {"other": "x"}, "targeted_validation_rule": "r", "test_type": "invalid"},
    ]

    class StubLLMService:
        def structured_generate(self, *, user, response_model, **kwargs):
            return response_model.model_validate({"datasets": raw})

    result = TestDataAgent(llm_service=StubLLMService()).run(_phone_state())

    rows = result.generated_test_cases[0].test_data_sets
    assert len(rows) == 20
    assert [row["phone"] for row in rows] == [f"invalid-{i}" for i in range(20)]


def test_test_data_agent_retries_llm_after_schema_failure():
    from backend.services.llm import LLMServiceError

    class StubLLMService:
        calls = 0

        def structured_generate(self, *, user, response_model, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise LLMServiceError("LLM response did not match TestCaseDataBatchResponse")
            return response_model.model_validate(
                {"datasets": [_phone_dataset(f"invalid-{i}") for i in range(20)]}
            )

    llm_service = StubLLMService()
    result = TestDataAgent(llm_service=llm_service).run(_phone_state())

    assert llm_service.calls == 2
    assert len(result.generated_test_cases[0].test_data_sets) == 20
    assert any("LLM test-data attempt failed" in log for log in result.logs)


def test_test_data_agent_raises_last_llm_error_when_every_attempt_fails():
    from backend.services.llm import LLMServiceError

    class StubLLMService:
        calls = 0

        def structured_generate(self, **kwargs):
            self.calls += 1
            raise LLMServiceError("bad shape")

    llm_service = StubLLMService()
    with pytest.raises(LLMServiceError, match="bad shape"):
        TestDataAgent(llm_service=llm_service).run(_phone_state())
    assert llm_service.calls == 3


def test_test_data_agent_collects_short_replies_until_20():
    """The live failure: a reply with only 18 datasets rejected the request.
    Short replies now add up across attempts."""
    replies = [
        [_phone_dataset(f"invalid-{i}") for i in range(18)],
        # Overlaps the first reply; only invalid-18 and invalid-19 are new.
        [_phone_dataset(f"invalid-{i}") for i in range(10, 20)],
    ]

    class StubLLMService:
        prompts = []

        def structured_generate(self, *, user, response_model, **kwargs):
            self.prompts.append(user)
            return response_model.model_validate({"datasets": replies.pop(0)})

    llm_service = StubLLMService()
    result = TestDataAgent(llm_service=llm_service).run(_phone_state())

    rows = result.generated_test_cases[0].test_data_sets
    assert [row["phone"] for row in rows] == [f"invalid-{i}" for i in range(20)]
    # The second prompt tells the model which datasets it already produced.
    assert TestDataAgent._dataset_fingerprint({"phone": "invalid-0"}) in llm_service.prompts[1]
