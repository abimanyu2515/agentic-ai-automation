import json

import pytest

from backend.agents.test_data_agent import (
    CASE_DATA_REQUESTS,
    TestCaseDataBatchResponse,
    PriceCatalog,
    TestCaseDataResponse,
    TestCaseFieldPlan,
    TestDataAgent,
)
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase
from backend.services.llm import LLMServiceError


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
            if response_model is TestCaseFieldPlan:
                raise LLMServiceError("no field plan in this test")
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
    # The 19 distinct datasets from the first reply are kept; only the one that
    # collided with reserved data is requested again.
    assert [row["phone"] for row in result.generated_test_data] == [
        *(f"invalid-{index}" for index in range(1, 20)),
        "invalid-100",
    ]
    assert "Return exactly 1 NEW datasets" in llm_service.prompts[1]
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
            if kwargs.get("response_model") is TestCaseFieldPlan:
                raise LLMServiceError("no field plan in this test")
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
            if kwargs.get("response_model") is TestCaseFieldPlan:
                raise LLMServiceError("no field plan in this test")
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


def _order_form_state():
    requirement = Requirement(title="Coffee order", description="Customers order two coffees.")
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Order form validation",
        description="Validate the coffee order form.",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Coffee order form validation",
        steps=["Choose two coffees", "Enter name and email", "Submit the order"],
        expected_result="The order is accepted",
    )
    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
    )
    return state, test_case


def _order_dataset(index, **overrides):
    data = {"coffee": f"Blend {index}", "email": f"buyer{index}@example.test"}
    data.update(overrides)
    return {
        "test_data": data,
        "targeted_validation_rule": "Valid order details are accepted.",
        "test_type": "positive",
        "expected_outcome": "accepted",
        "assumptions": [],
    }


class _QueuedReplies:
    """Returns each reply in turn; a reply can be an exception to raise.

    Field plan requests take from `plans`; with none left, they fail, and the
    data request plans its own fields. Price list requests work the same way
    with `catalog`.
    """

    def __init__(self, replies, plans=(), catalog=None):
        self.replies = list(replies)
        self.plans = list(plans)
        self.catalog = catalog
        self.prompts = []
        self.plan_prompts = []

    def structured_generate(self, *, user, response_model, **kwargs):
        if response_model is PriceCatalog:
            if self.catalog is None:
                raise LLMServiceError("no price list in this test")
            return self.catalog
        if response_model is TestCaseFieldPlan:
            self.plan_prompts.append(user)
            if not self.plans:
                raise LLMServiceError("no field plan in this test")
            return self.plans.pop(0)
        self.prompts.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_reply_with_59_datasets_keeps_the_first_20_distinct():
    """Live failure: 'List should have at most 20 items after validation, not 59'."""
    state, test_case = _order_form_state()
    reply = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_order_dataset(index) for index in range(59)]}
    )
    llm = _QueuedReplies([reply])

    result = TestDataAgent(llm_service=llm).run(state)

    assert len(llm.prompts) == 1
    assert len(test_case.test_data_sets) == 20
    assert [d["coffee"] for d in test_case.test_data_sets] == [f"Blend {i}" for i in range(20)]
    assert len(result.generated_test_data) == 20
    assert len(test_case.test_data_profiles) == 20


def test_malformed_item_is_dropped_and_the_rest_is_requested_again():
    """Live failure: one item had the key 'test_type: ' and the reply held 3 items."""
    state, test_case = _order_form_state()
    broken = (
        '{"datasets": ['
        + ",".join(__import__("json").dumps(_order_dataset(i)) for i in range(2))
        + ', {"test_data": {"coffee": "Mocha", "email": ""}, '
        '"targeted_validation_rule": "Email is required", "test_type: ": 1000000000000}]}'
    )
    first = TestCaseDataBatchResponse.model_validate_json(broken)
    second = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_order_dataset(index) for index in range(2, 20)]}
    )
    llm = _QueuedReplies([first, second])

    TestDataAgent(llm_service=llm).run(state)

    assert len(first.datasets) == 2
    assert len(llm.prompts) == 2
    assert "Return exactly 18 NEW datasets" in llm.prompts[1]
    assert '["coffee", "email"]' in llm.prompts[1]
    assert len({d["coffee"] for d in test_case.test_data_sets}) == 20


def test_llm_error_counts_as_one_request_not_a_failure():
    from backend.services.llm import LLMServiceError

    state, test_case = _order_form_state()
    good = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_order_dataset(index) for index in range(20)]}
    )
    llm = _QueuedReplies([LLMServiceError("reply did not match the schema"), good])

    TestDataAgent(llm_service=llm).run(state)

    assert len(llm.prompts) == 2
    assert len(test_case.test_data_sets) == 20


def test_rows_with_other_field_names_are_skipped():
    state, test_case = _order_form_state()
    rows = [_order_dataset(index) for index in range(20)]
    rows.insert(5, {**_order_dataset(99), "test_data": {"drink": "Tea"}})
    reply = TestCaseDataBatchResponse.model_validate({"datasets": rows})

    TestDataAgent(llm_service=_QueuedReplies([reply])).run(state)

    assert all(set(d) == {"coffee", "email"} for d in test_case.test_data_sets)
    assert len(test_case.test_data_sets) == 20


def test_gives_a_clear_error_when_every_request_fails():
    state, _ = _order_form_state()
    llm = _QueuedReplies([LLMServiceError("bad reply")] * CASE_DATA_REQUESTS)

    with pytest.raises(
        ValueError, match=f"after {CASE_DATA_REQUESTS} requests.*try again"
    ):
        TestDataAgent(llm_service=llm).run(state)
    assert len(llm.prompts) == CASE_DATA_REQUESTS


def test_follow_up_prompt_lists_accepted_datasets_as_values():
    """A model cannot avoid a hash it cannot invert, so show the real values."""
    state, _ = _order_form_state()
    first = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_order_dataset(index) for index in range(15)]}
    )
    rest = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_order_dataset(index) for index in range(15, 20)]}
    )
    llm = _QueuedReplies([first, rest])

    TestDataAgent(llm_service=llm).run(state)

    follow_up = llm.prompts[1]
    for dataset in first.datasets:
        assert json.dumps(dataset.test_data, ensure_ascii=False, separators=(",", ":")) in follow_up


def test_unquoted_numbers_and_label_variants_are_kept_not_dropped():
    """Live failure: follow-up replies kept 0 items because zip codes were JSON numbers."""
    reply = TestCaseDataBatchResponse.model_validate({"datasets": [
        {**_order_dataset(1), "test_data": {"coffee": "Latte", "email": None, "zip": 12345}},
        {**_order_dataset(2), "test_type": "Negative", "expected_outcome": " Rejected "},
        {**_order_dataset(3), "test_type": "Edge Case"},
        {**_order_dataset(4), "test_type": "nonsense"},
    ]})

    assert [d.test_type for d in reply.datasets] == ["positive", "invalid", "boundary"]
    assert reply.datasets[0].test_data == {"coffee": "Latte", "email": "", "zip": "12345"}
    assert reply.datasets[1].expected_outcome == "rejected"


def test_datasets_sent_as_json_strings_are_decoded():
    import json

    reply = TestCaseDataBatchResponse.model_validate(
        {"datasets": [json.dumps(_order_dataset(1)), "not json", _order_dataset(2)]}
    )

    assert [d.test_data["coffee"] for d in reply.datasets] == ["Blend 1", "Blend 2"]


def test_datasets_wrapped_in_an_extra_list_or_joined_in_one_string_are_unwrapped():
    import json

    one, two, three = (_order_dataset(i) for i in (1, 2, 3))
    reply = TestCaseDataBatchResponse.model_validate({"datasets": [
        [one, two],
        f"{json.dumps(three)}, {json.dumps(_order_dataset(4))}",
    ]})

    assert [d.test_data["coffee"] for d in reply.datasets] == [
        "Blend 1", "Blend 2", "Blend 3", "Blend 4",
    ]


def _booking_state():
    requirement = Requirement(
        title="Hotel booking",
        description="Guests register, search rooms, book, and pay.",
        business_rules=["Invalid card numbers are rejected."],
    )
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="End-to-end hotel booking",
        description="A new guest books and pays for a room.",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Guest completes a booking from sign-up to payment",
        steps=[
            "Register a new account",
            "Log in",
            "Search rooms for the stay dates",
            "Select a room and enter guest details",
            "Pay by card",
            "Confirm the booking",
        ],
        expected_result="Booking confirmation is shown",
        test_data={"email": "guest@example.test", "password": "Secret1!"},
    )
    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
    )
    return state, test_case


_BOOKING_FIELDS = ["email", "password", "check_in_date", "check_out_date", "card_number"]


def _booking_dataset(index, fields=_BOOKING_FIELDS):
    values = {
        "email": f"guest{index}@example.test",
        "password": f"Secret{index}!",
        "check_in_date": "2026-12-01",
        "check_out_date": "2026-12-03",
        "card_number": "4111111111111111",
    }
    return {
        "test_data": {field: values[field] for field in fields},
        "targeted_validation_rule": "A complete booking is confirmed.",
        "test_type": "positive",
        "expected_outcome": "accepted",
        "assumptions": [],
    }


def test_end_to_end_case_is_sent_to_the_llm_not_the_password_shortcut():
    state, test_case = _booking_state()
    reply = TestCaseDataBatchResponse.model_validate({
        "required_fields": _BOOKING_FIELDS,
        "datasets": [_booking_dataset(index) for index in range(20)],
    })
    llm = _QueuedReplies([reply])

    TestDataAgent(llm_service=llm).run(state)

    assert len(llm.prompts) == 1
    assert '"approved_test_data": {"email": "guest@example.test"' in llm.prompts[0]
    assert all(list(d) == _BOOKING_FIELDS for d in test_case.test_data_sets)
    assert not any(d["password"].isdigit() for d in test_case.test_data_sets)


def test_datasets_missing_later_step_fields_are_not_accepted():
    """A reply where most rows stop at the first form must not set the shape."""
    state, test_case = _booking_state()
    first_form_only = ["email", "password"]
    first = TestCaseDataBatchResponse.model_validate({
        "required_fields": _BOOKING_FIELDS,
        "datasets": [
            *(_booking_dataset(index, first_form_only) for index in range(12)),
            *(_booking_dataset(index) for index in range(100, 108)),
        ],
    })
    second = TestCaseDataBatchResponse.model_validate({
        "datasets": [_booking_dataset(index) for index in range(200, 212)],
    })
    llm = _QueuedReplies([first, second])

    TestDataAgent(llm_service=llm).run(state)

    assert "Return exactly 12 NEW datasets" in llm.prompts[1]
    assert json.dumps(_BOOKING_FIELDS) in llm.prompts[1]
    assert len(test_case.test_data_sets) == 20
    assert all(list(d) == _BOOKING_FIELDS for d in test_case.test_data_sets)


_CHECKOUT_FIELDS = [
    "sort_option", "selected_products", "expected_cart_count",
    "first_name", "last_name", "postal_code",
]


def _checkout_dataset(index, **overrides):
    data = {
        "sort_option": "Price (low to high)",
        "selected_products": "Sauce Labs Backpack; Sauce Labs Bike Light",
        "expected_cart_count": "2",
        "first_name": f"Buyer{index}",
        "last_name": "Synthetic",
        "postal_code": f"{10000 + index}",
    }
    data.update(overrides)
    return {
        "test_data": data,
        "targeted_validation_rule": "A valid checkout completes.",
        "test_type": "positive",
        "expected_outcome": "accepted",
        "assumptions": [],
    }


def test_positive_end_to_end_case_drops_rows_with_blank_or_negative_values():
    """Live failure: a valid Swag Labs checkout got rows with a blank first or last name."""
    requirement = Requirement(title="Swag Labs checkout", description="Buy products.")
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Complete purchase",
        description="A user buys products and completes checkout.",
        path_type="happy_path",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Complete end-to-end purchase",
        steps=[
            "Navigate to the Products Page",
            "Sort products by name and price",
            "Add selected products to the cart",
            "Verify the cart item count updates correctly",
            "Proceed to checkout",
            "Enter valid First Name, Last Name, and Zip/Postal Code",
            "Click the Finish button",
        ],
        expected_result="The success confirmation message and image are shown",
    )
    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
    )
    first = TestCaseDataBatchResponse.model_validate({
        "required_fields": _CHECKOUT_FIELDS,
        "datasets": [
            *(_checkout_dataset(index) for index in range(16)),
            _checkout_dataset(16, first_name=""),
            _checkout_dataset(17, last_name=""),
            {**_checkout_dataset(18), "test_type": "invalid", "expected_outcome": "rejected"},
            {**_checkout_dataset(19), "test_type": "missing_required"},
        ],
    })
    second = TestCaseDataBatchResponse.model_validate({
        "datasets": [_checkout_dataset(index) for index in range(100, 104)],
    })
    llm = _QueuedReplies([first, second])

    TestDataAgent(llm_service=llm).run(state)

    assert "This is a positive (happy-path) case" in llm.prompts[0]
    assert "Return exactly 4 NEW datasets" in llm.prompts[1]
    assert len(test_case.test_data_sets) == 20
    assert all(list(d) == _CHECKOUT_FIELDS for d in test_case.test_data_sets)
    assert all(all(d.values()) for d in test_case.test_data_sets)
    assert all(
        profile["expected_outcome"] == "accepted"
        for profile in test_case.test_data_profiles
    )


def _checkout_state():
    requirement = Requirement(title="Swag Labs checkout", description="Buy products.")
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Complete purchase",
        description="A user buys products and completes checkout.",
        path_type="happy_path",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Complete end-to-end purchase",
        steps=[
            "Navigate to the Products Page",
            "Sort products by name and price",
            "Add selected products to the cart",
            "Verify the cart item count updates correctly",
            "Proceed to checkout",
            "Enter valid First Name, Last Name, and Zip/Postal Code",
            "Click the Finish button",
        ],
        expected_result="The success confirmation message and image are shown",
    )
    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
    )
    return state, test_case


_CHECKOUT_PLAN = {
    "steps": [
        {"step_number": 1, "fields": []},
        {"step_number": 2, "fields": ["sort_option"]},
        {"step_number": 3, "fields": ["selected_products"]},
        {"step_number": 4, "fields": ["expected_cart_count"]},
        {"step_number": 5, "fields": []},
        {"step_number": 6, "fields": ["first_name", "last_name", "postal_code"]},
        {"step_number": 7, "fields": []},
    ],
}


def test_field_plan_fixes_every_field_from_the_first_step_to_the_last():
    state, test_case = _checkout_state()
    plan = TestCaseFieldPlan.model_validate(_CHECKOUT_PLAN)
    reply = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_checkout_dataset(index) for index in range(20)]}
    )
    llm = _QueuedReplies([reply], plans=[plan])

    TestDataAgent(llm_service=llm).run(state)

    assert len(llm.plan_prompts) == 1
    assert json.dumps(_CHECKOUT_FIELDS) in llm.prompts[0]
    assert (
        "Step 6 (Enter valid First Name, Last Name, and Zip/Postal Code): "
        "first_name, last_name, postal_code"
    ) in llm.prompts[0]
    assert all(list(d) == _CHECKOUT_FIELDS for d in test_case.test_data_sets)


def test_field_plan_is_requested_again_when_an_input_step_has_no_fields():
    state, test_case = _checkout_state()
    stops_early = TestCaseFieldPlan.model_validate({"steps": _CHECKOUT_PLAN["steps"][:4]})
    complete = TestCaseFieldPlan.model_validate(_CHECKOUT_PLAN)
    reply = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_checkout_dataset(index) for index in range(20)]}
    )
    llm = _QueuedReplies([reply], plans=[stops_early, complete])

    TestDataAgent(llm_service=llm).run(state)

    assert len(llm.plan_prompts) == 2
    assert "step 6: Enter valid First Name" in llm.plan_prompts[1]
    assert set(test_case.test_data_sets[0]) == set(_CHECKOUT_FIELDS)


def test_rows_missing_a_planned_field_are_dropped_and_names_are_normalized():
    state, test_case = _checkout_state()
    plan = TestCaseFieldPlan.model_validate(_CHECKOUT_PLAN)
    rows = [_checkout_dataset(index) for index in range(20)]
    # "Postal Code" is the planned postal_code; a row without it stops early.
    rows[0]["test_data"]["Postal Code"] = rows[0]["test_data"].pop("postal_code")
    for row in rows[1:6]:
        del row["test_data"]["postal_code"]
    first = TestCaseDataBatchResponse.model_validate({"datasets": rows})
    rest = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_checkout_dataset(index) for index in range(100, 105)]}
    )
    llm = _QueuedReplies([first, rest], plans=[plan])

    TestDataAgent(llm_service=llm).run(state)

    assert "Return exactly 5 NEW datasets" in llm.prompts[1]
    assert len(test_case.test_data_sets) == 20
    assert all(set(d) == set(_CHECKOUT_FIELDS) for d in test_case.test_data_sets)


def test_valid_login_gets_20_vault_datasets_without_calling_the_llm():
    """Live failure: 19 invented "valid" accounts, including locked_out_user."""
    requirement = Requirement(title="Login", description="Users log in.")
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Valid credentials successful login",
        description="A user logs in with valid credentials.",
        path_type="happy_path",
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Verify login with valid credentials",
        preconditions=["User has a valid account registered in the system"],
        steps=[
            "Navigate to the application login page",
            "Enter the valid username into the username field",
            "Enter the valid password into the password field",
            "Click the login button",
        ],
        expected_result="The user is redirected to the home page.",
    )
    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
    )
    llm = _QueuedReplies([])

    TestDataAgent(llm_service=llm).run(state)

    assert llm.prompts == [] and llm.plan_prompts == []
    assert len(test_case.test_data_sets) == 20
    assert len({json.dumps(d, sort_keys=True) for d in test_case.test_data_sets}) == 20
    assert test_case.test_data_sets[0] == {
        "username": "{{vault:TARGET_USERNAME}} run 01",
        "password": "{{vault:TARGET_PASSWORD}} run 01",
    }
    assert all(
        profile["expected_outcome"] == "accepted"
        for profile in test_case.test_data_profiles
    )


def test_positive_login_flow_gets_vault_credentials_plus_its_other_fields():
    """A valid login followed by a checkout keeps every field; credentials
    come from the vault, not from the model."""
    state, test_case = _checkout_state()
    test_case.steps = [
        "Enter the valid username and password and click Login",
        *test_case.steps,
    ]
    rows = [
        _checkout_dataset(index, username=f"user_{index}", password=f"pw_{index}")
        for index in range(20)
    ]
    reply = TestCaseDataBatchResponse.model_validate({"datasets": rows})

    TestDataAgent(llm_service=_QueuedReplies([reply])).run(state)

    assert len(test_case.test_data_sets) == 20
    assert all(
        list(d) == ["username", "password", *_CHECKOUT_FIELDS]
        for d in test_case.test_data_sets
    )
    assert {d["username"] for d in test_case.test_data_sets} == {"{{vault:TARGET_USERNAME}}"}
    assert {d["password"] for d in test_case.test_data_sets} == {"{{vault:TARGET_PASSWORD}}"}


def test_checkout_drops_invented_login_fields_and_guessed_tax_values():
    """Live failure: checkout rows carried user_a_01/pass_user_a and a guessed tax rate."""
    state, test_case = _checkout_state()
    test_case.preconditions = ["User is logged into the application"]
    rows = [
        _checkout_dataset(
            index,
            username=f"user_{index:02d}",
            password=f"pass_{index:02d}",
            expected_tax_rate="0.08",
        )
        for index in range(20)
    ]
    reply = TestCaseDataBatchResponse.model_validate({"datasets": rows})

    TestDataAgent(llm_service=_QueuedReplies([reply])).run(state)

    assert len(test_case.test_data_sets) == 20
    assert all(list(d) == _CHECKOUT_FIELDS for d in test_case.test_data_sets)


def test_rows_whose_expected_count_does_not_match_their_products_are_dropped():
    state, test_case = _checkout_state()
    plan = TestCaseFieldPlan.model_validate(_CHECKOUT_PLAN)
    rows = [_checkout_dataset(index) for index in range(20)]
    for row in rows[:3]:
        row["test_data"]["expected_cart_count"] = "3"  # two products listed
    first = TestCaseDataBatchResponse.model_validate({"datasets": rows})
    rest = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_checkout_dataset(index) for index in range(100, 103)]}
    )
    llm = _QueuedReplies([first, rest], plans=[plan])

    TestDataAgent(llm_service=llm).run(state)

    assert "Return exactly 3 NEW datasets" in llm.prompts[1]
    assert all(d["expected_cart_count"] == "2" for d in test_case.test_data_sets)


_SAUCE_CATALOG = {
    "products": [
        {"name": "Sauce Labs Backpack", "price": "$29.99"},
        {"name": "Sauce Labs Bike Light", "price": 9.99},
        {"name": "Sauce Labs Onesie", "price": "7.99"},
    ],
    "tax_rate": "8%",
    "source": "known application",
}


def test_checkout_rows_get_totals_computed_from_one_price_list():
    """Live failure: a guessed tax rate that changed from row to row."""
    state, test_case = _checkout_state()
    test_case.steps = [*test_case.steps, "Verify item total, tax, and total"]
    rows = [
        _checkout_dataset(
            index,
            selected_products="sauce labs backpack; Sauce Labs Bike Light",
            expected_tax_rate=f"0.0{index % 9}",
        )
        for index in range(20)
    ]
    rows[0]["test_data"]["selected_products"] = "Sauce Labs Backpack; Unknown Mug"
    first = TestCaseDataBatchResponse.model_validate({"datasets": rows})
    rest = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_checkout_dataset(100, selected_products="Sauce Labs Onesie; Sauce Labs Backpack")]}
    )
    llm = _QueuedReplies(
        [first, rest], catalog=PriceCatalog.model_validate(_SAUCE_CATALOG)
    )

    TestDataAgent(llm_service=llm).run(state)

    assert "Sauce Labs Backpack; Sauce Labs Bike Light; Sauce Labs Onesie" in llm.prompts[0]
    first_row = test_case.test_data_sets[0]
    assert first_row["selected_products"] == "Sauce Labs Backpack; Sauce Labs Bike Light"
    assert list(first_row)[-3:] == ["expected_item_total", "expected_tax", "expected_total"]
    # 29.99 + 9.99 = 39.98; 8% tax = 3.1984 -> 3.20; total 43.18
    assert (first_row["expected_item_total"], first_row["expected_tax"], first_row["expected_total"]) == (
        "39.98", "3.20", "43.18"
    )
    last_row = test_case.test_data_sets[-1]
    assert (last_row["expected_item_total"], last_row["expected_tax"], last_row["expected_total"]) == (
        "37.98", "3.04", "41.02"
    )
    assert "expected_tax_rate" not in first_row


def test_checkout_without_a_known_price_list_gets_no_guessed_totals():
    state, test_case = _checkout_state()
    test_case.steps = [*test_case.steps, "Verify item total, tax, and total"]
    reply = TestCaseDataBatchResponse.model_validate(
        {"datasets": [_checkout_dataset(index, expected_total="99.99") for index in range(20)]}
    )

    TestDataAgent(llm_service=_QueuedReplies([reply])).run(state)

    assert all(list(d) == _CHECKOUT_FIELDS for d in test_case.test_data_sets)
