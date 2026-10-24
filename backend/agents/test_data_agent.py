"""
TestDataAgent

Purpose:
    Generates a test-data plan and a synthetic dataset for a feature based on
    available requirement, scenario, and test-case context.

Responsibilities:
    - Read the requirement and any approved scenarios/test cases already in the
      workflow state.
    - Identify the fields and behaviors that should be exercised.
    - Produce a representative combination matrix.
    - Generate a CSV-ready 20-row requirement dataset or one validation-targeted
      dataset for a selected test case.
    - Record assumptions when the real validation rules are not supplied.

This agent intentionally follows the repository's state-based conventions and
reports structured output as data that downstream agents can parse. It does not
register itself in the workflow graph automatically; integration remains a
separate concern.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import re
from uuid import uuid4
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.agents.base import BaseAgent
from backend.models.requirement import Requirement
from backend.models.state import WorkflowState
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase
from backend.services.llm import LLMService, LLMServiceError
from backend.utils.prompts import load_prompt


class TestDataCombination(BaseModel):
    """Single test combination for review and downstream parsing."""

    combination_id: str = Field(pattern=r"^COMB-\d{3}$")
    fields_dimensions: str = Field(min_length=1)
    values_condition: str = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)
    priority: str = Field(default="Medium")
    rationale: str = Field(min_length=1)


class TestDataReport(BaseModel):
    """Structured artifact that mirrors the specification's required sections."""

    context_understood: dict[str, Any] = Field(default_factory=dict)
    testable_combinations: list[TestDataCombination] = Field(default_factory=list)
    sample_dataset: dict[str, Any] = Field(default_factory=dict)
    coverage_summary: dict[str, Any] = Field(default_factory=dict)


class TestCaseDataResponse(BaseModel):
    """One data set tailored to a single test case's validation behavior."""

    test_data: dict[str, str] = Field(min_length=1)
    targeted_validation_rule: str = Field(min_length=1)
    test_type: Literal[
        "positive",
        "invalid",
        "boundary",
        "missing_required",
        "workflow",
    ]
    assumptions: list[str] = Field(default_factory=list)


class TestCaseDataBatchResponse(BaseModel):
    """Exactly 20 distinct datasets for the same selected test case."""

    datasets: list[TestCaseDataResponse] = Field(min_length=20, max_length=20)


class TestDataAgent(BaseAgent):
    """Generates requirement-level data plans and unique per-case test data."""

    name = "TestData Agent"

    def __init__(self, llm_service: LLMService | None = None):
        self.llm_service = llm_service or LLMService()

    def run(self, state: WorkflowState) -> WorkflowState:
        if state.requirement is None:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        state.add_log(f"{self.name} started")

        if len(state.generated_test_cases) == 1:
            return self._run_for_test_case(state)

        report = self._generate_report(state.requirement)
        state.test_data_report = report.model_dump(mode="python")
        state.test_data_combinations = [item.model_dump(mode="python") for item in report.testable_combinations]
        state.generated_test_data = report.sample_dataset.get("rows", [])

        state.add_log(f"{self.name} created a test-data plan with {len(state.generated_test_data)} rows")
        return state

    def _run_for_test_case(self, state: WorkflowState) -> WorkflowState:
        requirement = state.requirement
        test_case = state.generated_test_cases[0]
        scenario = next(
            (
                item for item in state.generated_scenarios
                if item.id == test_case.scenario_id
            ),
            None,
        )
        if scenario is None:
            raise ValueError(
                f"{self.name} requires the selected test case's scenario"
            )

        context = state.testing_context or {}
        reserved_datasets = context.get("reserved_test_data", [])
        if not isinstance(reserved_datasets, list):
            raise ValueError(f"{self.name} received invalid reserved test-data context")
        reserved_fingerprints = {
            self._dataset_fingerprint(dataset)
            for dataset in reserved_datasets
            if isinstance(dataset, dict) and dataset
        }
        validation_rules = [
            *requirement.functional_requirements,
            *requirement.business_rules,
            *requirement.acceptance_criteria,
        ]
        source_test_data = self._scenario_test_data(scenario)
        generated_datasets = self._generate_deterministic_case_data(
            requirement=requirement,
            scenario=scenario,
            test_case=test_case,
            source_test_data=source_test_data,
            validation_rules=validation_rules,
            reserved_fingerprints=reserved_fingerprints,
        )
        collision_fingerprints: set[str] = set()
        for _ in (range(3) if generated_datasets is None else []):
            application_context = context.get("application_context", {})
            if not isinstance(application_context, dict):
                raise ValueError(f"{self.name} received invalid application context")
            user_prompt = load_prompt(
                "test_case_data_prompt.txt",
                requirement_title=requirement.title,
                requirement_description=requirement.description,
                application_context=json.dumps(
                    application_context,
                    ensure_ascii=False,
                ),
                validation_rules=json.dumps(validation_rules, ensure_ascii=False),
                scenario=json.dumps(
                    {
                        "name": scenario.scenario_name,
                        "description": scenario.description,
                    },
                    ensure_ascii=False,
                ),
                test_case=json.dumps(
                    {
                        "id": str(test_case.id),
                        "title": test_case.title,
                        "preconditions": test_case.preconditions,
                        "steps": test_case.steps,
                        "expected_result": test_case.expected_result,
                    },
                    ensure_ascii=False,
                ),
                reserved_dataset_fingerprints=json.dumps(
                    sorted(reserved_fingerprints | collision_fingerprints)
                ),
                uniqueness_nonce=uuid4().hex,
            )
            try:
                candidate = self.llm_service.structured_generate(
                    user=user_prompt,
                    response_model=TestCaseDataBatchResponse,
                    max_retries=1,
                    timeout=240,
                )
            except LLMServiceError:
                generated_datasets = self._generate_deterministic_case_data(
                    requirement=requirement,
                    scenario=scenario,
                    test_case=test_case,
                    source_test_data=source_test_data,
                    validation_rules=validation_rules,
                    reserved_fingerprints=reserved_fingerprints,
                )
                if generated_datasets is None:
                    raise
                break
            candidate_fingerprints = [
                self._dataset_fingerprint(dataset.test_data)
                for dataset in candidate.datasets
            ]
            if any(
                not dataset.test_data
                or any(not key.strip() for key in dataset.test_data)
                for dataset in candidate.datasets
            ):
                raise ValueError(
                    f"{self.name} returned a dataset without usable input fields"
                )
            field_names = set(candidate.datasets[0].test_data)
            if any(set(dataset.test_data) != field_names for dataset in candidate.datasets):
                raise ValueError(
                    f"{self.name} returned inconsistent fields across the 20 datasets"
                )
            if (
                len(set(candidate_fingerprints)) == 20
                and not reserved_fingerprints.intersection(candidate_fingerprints)
            ):
                generated_datasets = candidate.datasets
                break
            collision_fingerprints.update(candidate_fingerprints)

        if generated_datasets is None:
            raise ValueError(
                f"{self.name} could not generate 20 unique datasets for "
                f"'{test_case.title}' after three attempts"
            )

        input_datasets = [dataset.test_data for dataset in generated_datasets]
        data_ids = [
            f"TD-{test_case.id.hex[:8].upper()}-{index:02d}"
            for index in range(1, len(input_datasets) + 1)
        ]
        rows = [
            {
                "test_data_id": data_id,
                **dataset.test_data,
                "targeted_validation_rule": dataset.targeted_validation_rule,
                "expected_result": test_case.expected_result,
                "test_type": dataset.test_type,
            }
            for data_id, dataset in zip(data_ids, generated_datasets)
        ]
        header = list(rows[0])
        csv_buffer = io.StringIO()
        writer = csv.DictWriter(csv_buffer, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)

        report = TestDataReport(
            context_understood={
                "requirement": {
                    "id": str(requirement.id),
                    "title": requirement.title,
                },
                "test_case": {
                    "id": str(test_case.id),
                    "title": test_case.title,
                },
                "test_data_ids": data_ids,
                "important_rules_and_constraints": validation_rules,
                "targeted_validation_rule": generated_datasets[0].targeted_validation_rule,
                "expected_result": test_case.expected_result,
                "assumptions_and_questions": sorted({
                    assumption
                    for dataset in generated_datasets
                    for assumption in dataset.assumptions
                }),
                "unique_for_test_case": True,
                "number_of_datasets": len(input_datasets),
            },
            testable_combinations=[],
            sample_dataset={
                "header": header,
                "rows": rows,
                "csv_text": csv_buffer.getvalue(),
            },
            coverage_summary={
                "number_of_data_rows": len(rows),
                "positive_cases": sum(dataset.test_type == "positive" for dataset in generated_datasets),
                "negative_cases": sum(dataset.test_type in {
                    "invalid", "missing_required", "workflow"
                } for dataset in generated_datasets),
                "boundary_cases": sum(dataset.test_type == "boundary" for dataset in generated_datasets),
                "unique_dataset": True,
            },
        )
        state.test_data_report = report.model_dump(mode="python")
        state.test_data_combinations = []
        state.generated_test_data = rows
        test_case.test_data = input_datasets[0]
        test_case.test_data_sets = input_datasets
        state.add_log(
            f"{self.name} generated {len(input_datasets)} unique datasets "
            f"for test case '{test_case.title}'"
        )
        return state

    @classmethod
    def _generate_deterministic_case_data(
        cls,
        *,
        requirement: Requirement,
        scenario: Scenario,
        test_case: TestCase,
        source_test_data: dict[str, str],
        validation_rules: list[str],
        reserved_fingerprints: set[str],
    ) -> list[TestCaseDataResponse] | None:
        metadata_fields = {
            "test_data_id",
            "targeted_validation_rule",
            "expected_result",
            "test_type",
        }
        base_data = {
            key: value
            for key, value in (test_case.test_data or source_test_data).items()
            if key not in metadata_fields
        }
        narrative = " ".join(
            [
                requirement.title,
                requirement.description,
                scenario.scenario_name,
                scenario.description,
                test_case.title,
                test_case.expected_result,
                *test_case.preconditions,
                *test_case.steps,
                *[str(key) for key in source_test_data],
                *[str(value) for value in source_test_data.values()],
                *[str(key) for key in test_case.test_data],
                *[str(value) for value in test_case.test_data.values()],
                *validation_rules,
            ]
        ).lower()
        case_focus = " ".join(
            [
                scenario.scenario_name,
                test_case.title,
                test_case.expected_result,
                *test_case.preconditions,
                str(test_case.test_data.get("targeted_validation_rule", "")),
                str(source_test_data.get("targeted_validation_rule", "")),
            ]
        ).lower()
        login_focus = " ".join([case_focus, *test_case.steps]).lower()
        login_datasets = cls._generate_deterministic_login_data(
            case_focus=login_focus,
            base_data=base_data,
            reserved_fingerprints=reserved_fingerprints,
        )
        if login_datasets is not None:
            return login_datasets

        invalid_context = any(
            marker in narrative
            for marker in ("invalid", "reject", "fail", "only numeric", "digits only")
        )
        if not invalid_context:
            return None

        target_field = None
        target_rule = None
        value_factory = None
        password_field = next(
            (field for field in base_data if "password" in field.lower()),
            None,
        )
        email_field = next(
            (field for field in base_data if "email" in field.lower()),
            None,
        )
        email_focus = "email" in case_focus and any(
            marker in case_focus
            for marker in ("invalid", "malformed", "reject", "incorrect")
        )
        numeric_password = (
            "password" in narrative
            and any(
                marker in narrative
                for marker in ("numeric-only", "only numeric", "digits only", "numbers only")
            )
        ) or (
            password_field is not None
            and bool(re.fullmatch(r"\d+", base_data[password_field]))
        ) or (
            password_field is not None
            and invalid_context
            and not email_focus
            and not any(
                term in case_focus
                for term in ("login", "log in", "sign in", "signin")
            )
        )
        if numeric_password:
            target_field = password_field or "password"
            target_rule = (
                source_test_data.get("targeted_validation_rule")
                or "Password contains only numeric characters."
            )

            def value_factory(index: int) -> str:
                return f"{index:08d}"

        elif "email" in narrative and (
            "invalid" in narrative
            or "malformed" in narrative
            or (email_field is not None and "@" not in base_data[email_field])
        ):
            target_field = email_field or "email"
            target_rule = "Email address is malformed and must be rejected."

            def value_factory(index: int) -> str:
                return f"invalid-email-{index:02d}"

        if target_field is None or target_rule is None or value_factory is None:
            return None

        datasets = []
        fingerprints = set(reserved_fingerprints)
        for index in range(1, 1001):
            candidate = {**base_data, target_field: value_factory(index)}
            if numeric_password and "confirm_password" in candidate:
                candidate["confirm_password"] = candidate[target_field]
            fingerprint = cls._dataset_fingerprint(candidate)
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            datasets.append(
                TestCaseDataResponse(
                    test_data=candidate,
                    targeted_validation_rule=target_rule,
                    test_type="invalid",
                    assumptions=[
                        "Generated locally because the configured LLM did not return a valid dataset batch."
                    ],
                )
            )
            if len(datasets) == 20:
                return datasets
        return None

    @classmethod
    def _generate_deterministic_login_data(
        cls,
        *,
        case_focus: str,
        base_data: dict[str, str],
        reserved_fingerprints: set[str],
    ) -> list[TestCaseDataResponse] | None:
        if not any(term in case_focus for term in ("login", "log in", "sign in", "signin")):
            return None

        username_field = next(
            (
                field for field in base_data
                if field.lower().replace("-", "_") in {"username", "user_name", "user"}
            ),
            "username",
        )
        password_field = next(
            (field for field in base_data if "password" in field.lower()),
            "password",
        )
        username_empty_markers = (
            "empty username", "username empty", "blank username", "username blank",
            "missing username", "username is required", "username required",
            "leave username blank", "leave username empty",
        )
        password_empty_markers = (
            "empty password", "password empty", "blank password", "password blank",
            "missing password", "password is required", "password required",
            "leave password blank", "leave password empty",
        )
        username_invalid_markers = (
            "invalid username", "incorrect username", "wrong username",
            "unknown username", "unrecognized username", "username is invalid",
            "username is incorrect", "username is wrong",
        )
        password_invalid_markers = (
            "invalid password", "incorrect password", "wrong password",
            "password is invalid", "password is incorrect", "password is wrong",
        )

        username_empty = (
            username_field in base_data and not base_data[username_field].strip()
        ) or (
            any(marker in case_focus for marker in username_empty_markers)
        )
        password_empty = (
            password_field in base_data and not base_data[password_field].strip()
        ) or (
            any(marker in case_focus for marker in password_empty_markers)
        )
        invalid_username = any(
            marker in case_focus for marker in username_invalid_markers
        )
        invalid_password = any(
            marker in case_focus for marker in password_invalid_markers
        )

        if username_empty:
            target_field = username_field
            target_rule = "Username is required and cannot be empty."
            value_factory = lambda index: ""
            varied_field = password_field
            varied_factory = lambda index: f"synthetic-password-{index:03d}"
            test_type = "missing_required"
        elif password_empty:
            target_field = password_field
            target_rule = "Password is required and cannot be empty."
            value_factory = lambda index: ""
            varied_field = username_field
            varied_factory = lambda index: f"synthetic-user-{index:03d}"
            test_type = "missing_required"
        elif invalid_username:
            target_field = username_field
            target_rule = "Unknown usernames must be rejected."
            value_factory = lambda index: f"invalid-user-{index:03d}"
            varied_field = None
            varied_factory = None
            test_type = "invalid"
        elif invalid_password:
            target_field = password_field
            target_rule = "Incorrect passwords must be rejected."
            value_factory = lambda index: f"invalid-password-{index:03d}"
            varied_field = None
            varied_factory = None
            test_type = "invalid"
        else:
            return None

        datasets: list[TestCaseDataResponse] = []
        fingerprints = set(reserved_fingerprints)
        login_data = dict(base_data)
        login_data[username_field] = "synthetic-user"
        login_data[password_field] = "synthetic-password"
        for index in range(1, 1001):
            candidate = dict(login_data)
            if varied_field is not None and varied_factory is not None:
                candidate[varied_field] = varied_factory(index)
            candidate[target_field] = value_factory(index)
            fingerprint = cls._dataset_fingerprint(candidate)
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            datasets.append(
                TestCaseDataResponse(
                    test_data=candidate,
                    targeted_validation_rule=target_rule,
                    test_type=test_type,
                    assumptions=[
                        "Generated synthetic login inputs for the selected test case; no account credentials were used."
                    ],
                )
            )
            if len(datasets) == 20:
                return datasets
        return None

    @staticmethod
    def _scenario_test_data(scenario: Scenario) -> dict[str, str]:
        marker = "generated input data "
        start = scenario.description.find(marker)
        if start < 0:
            return {}
        literal_start = scenario.description.find(": ", start)
        literal_end = scenario.description.find(". Expected result:", literal_start)
        if literal_start < 0 or literal_end < 0:
            return {}
        try:
            data = ast.literal_eval(
                scenario.description[literal_start + 2:literal_end]
            )
        except (SyntaxError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in data.items()
            if isinstance(value, (str, int, float, bool))
        }

    @staticmethod
    def _dataset_fingerprint(dataset: dict[str, str]) -> str:
        normalized = json.dumps(
            dataset,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _generate_report(self, requirement: Requirement) -> TestDataReport:
        fields = self._detect_fields(requirement)
        context = self._build_context(requirement, fields)
        combinations = self._build_combinations(fields)
        dataset = self._build_dataset(fields)
        coverage = self._build_coverage_summary(dataset["rows"])

        return TestDataReport(
            context_understood=context,
            testable_combinations=combinations,
            sample_dataset=dataset,
            coverage_summary=coverage,
        )

    def _detect_fields(self, requirement: Requirement) -> list[str]:
        combined_text = " ".join(
            filter(
                None,
                [
                    requirement.title,
                    requirement.description,
                    *requirement.functional_requirements,
                    *requirement.business_rules,
                    *requirement.acceptance_criteria,
                ],
            )
        )
        text = combined_text.lower()

        preferred = [
            "full_name",
            "email",
            "password",
            "confirm_password",
            "accept_terms",
            "username",
            "phone_number",
            "status",
            "amount",
            "date",
            "country",
            "city",
            "postal_code",
            "product_id",
            "account_id",
        ]

        aliases = {
            "full_name": r"\bfull[\s_-]+name\b",
            "confirm_password": r"\bconfirm(?:ation)?[\s_-]+password\b|\bpassword[\s_-]+confirmation\b",
            "accept_terms": r"\baccept(?:ed|ance)?[\s_-]+(?:the[\s_-]+)?terms\b|\bterms\b.*\b(?:accept(?:ed|ance)?|agree|checkbox)\b",
        }
        matches = []
        for field in preferred:
            pattern = aliases.get(field)
            if pattern is None:
                pattern = r"\b" + re.escape(field).replace("_", r"[\s_-]+") + r"\b"
            if re.search(pattern, text):
                matches.append(field)
        if not matches:
            matches = ["full_name", "email", "password", "confirm_password", "accept_terms"]

        return list(dict.fromkeys(matches))[:6]

    def _build_context(self, requirement: Requirement, fields: list[str]) -> dict[str, Any]:
        rules = []
        if requirement.business_rules:
            rules.extend(requirement.business_rules)
        if requirement.acceptance_criteria:
            rules.extend(requirement.acceptance_criteria)
        if not rules:
            rules.append("No explicit validation rules were supplied; values below are assumptions for a useful draft and must be confirmed against the real application schema.")

        assumptions = []
        if requirement.functional_requirements:
            assumptions.append("Functional requirements were used as the primary source for input and outcome coverage.")
        else:
            assumptions.append("The feature is treated as a generic form flow because no detailed field schema was supplied.")
        assumptions.append("All sample data is synthetic and intentionally excludes any real customer data, secrets, or credentials.")

        return {
            "requirement": {
                "id": str(requirement.id),
                "title": requirement.title,
                "description": requirement.description,
            },
            "source_material_used": [
                "Requirement title",
                "Requirement description",
                *(["Functional requirements"] if requirement.functional_requirements else []),
                *(["Business rules"] if requirement.business_rules else []),
                *(["Acceptance criteria"] if requirement.acceptance_criteria else []),
            ],
            "important_rules_and_constraints": rules,
            "fields_identified": fields,
            "assumptions_and_questions": assumptions,
        }

    def _build_combinations(self, fields: list[str]) -> list[TestDataCombination]:
        field_summary = ", ".join(fields)
        combinations = [
            TestDataCombination(
                combination_id="COMB-001",
                fields_dimensions=field_summary,
                values_condition="All identified fields populated with valid values",
                expected_behavior="Valid data should pass all required validation and reach the intended success path.",
                priority="High",
                rationale="Verifies the normal happy path and confirms the base acceptance criteria.",
            ),
            TestDataCombination(
                combination_id="COMB-002",
                fields_dimensions=field_summary,
                values_condition="Boundary values at the documented minimum or maximum acceptable limits",
                expected_behavior="Boundary values should either pass or fail according to the actual requirement; if no explicit limit is supplied, this row is a placeholder for the real boundary decision.",
                priority="High",
                rationale="Catches off-by-one and validation-limit errors.",
            ),
            TestDataCombination(
                combination_id="COMB-003",
                fields_dimensions=field_summary,
                values_condition="Malformed or unsupported values in key fields",
                expected_behavior="Invalid values should be rejected with a clear validation error instead of silently being saved.",
                priority="High",
                rationale="Protects negative-path coverage and ensures validation rules can be tested.",
            ),
            TestDataCombination(
                combination_id="COMB-004",
                fields_dimensions=field_summary,
                values_condition="Missing required field or optional field left blank",
                expected_behavior="Missing required data should be rejected; optional fields may be left empty when allowed by the requirement.",
                priority="Medium",
                rationale="Confirms required-vs-optional handling and form validation behavior.",
            ),
            TestDataCombination(
                combination_id="COMB-005",
                fields_dimensions=field_summary,
                values_condition="Field-to-field dependency such as password and confirm password or duplicate record attempts",
                expected_behavior="Cross-field validation should enforce consistency and prevent duplicate or contradictory submissions.",
                priority="Medium",
                rationale="Exercises relationship logic between multiple fields and workflow state.",
            ),
        ]
        return combinations

    def _build_dataset(self, fields: list[str]) -> dict[str, Any]:
        rows: list[dict[str, str]] = []

        examples = {
            "full_name": ["Alice Johnson", "Bob Smith", "Carmen Diaz", "Daniel Kim", "Evelyn Garcia", "Frank Lee", "Grace Patel", "Henry Chen", "Ivy Nguyen", "Jack Moore", "Karen Wang", "Leo Martin", "Mia Thompson", "Noah White", "Olivia Brown", "Paul Clark", "Quinn Evans", "Ruby Hall", "Sam Lewis", "Tina Young"],
            "email": ["alice@example.com", "bob@example.com", "carmen@example.com", "daniel@example.com", "evelyn@example.com", "frank@example.com", "grace@example.com", "henry@example.com", "ivy@example.com", "jack@example.com", "karen@example.com", "leo@example.com", "mia@example.com", "noah@example.com", "olivia@example.com", "paul@example.com", "quinn@example.com", "ruby@example.com", "sam@example.com", "tina@example.com"],
            "password": ["Passw0rd!", "StrongPass2!", "Secure123!", "M!nimum9", "LongerP@ss9", "Abcdef1!", "ValidPass!7", "Complex$4", "Password1!", "P@ssword99", "C0mplex!9", "Pass12@word", "Qwerty!1", "GreenTea!8", "Orange38$", "BlueSky!7", "Night!23", "Sunset@5", "RainBow!3", "Lakeside#1"],
            "confirm_password": ["Passw0rd!", "StrongPass2!", "Secure123!", "M!nimum9", "LongerP@ss9", "Abcdef1!", "ValidPass!7", "Complex$4", "Password1!", "P@ssword99", "C0mplex!9", "Pass12@word", "Qwerty!1", "GreenTea!8", "Orange38$", "BlueSky!7", "Night!23", "Sunset@5", "RainBow!3", "Lakeside#1"],
            "accept_terms": ["true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true", "true"],
            "username": ["alicej", "bobsmith", "carmen_d", "danielk", "evelyng", "frankl", "gracep", "henryc", "ivyn", "jackm", "karenw", "leom", "miat", "noahw", "olivia", "paulc", "quinn_e", "rubyh", "saml", "tinay"],
            "status": ["active", "inactive", "pending", "active", "inactive", "pending", "active", "inactive", "pending", "active", "inactive", "pending", "active", "inactive", "pending", "active", "inactive", "pending", "active", "inactive"],
            "amount": ["10.00", "25.00", "49.99", "50.00", "99.99", "100.00", "250.00", "999.99", "1000.00", "10000.00", "-1.00", "0.00", "1.00", "9999.99", "abc", "invalid", "", "100.00", "50.00", "101.00"],
            "date": ["2025-01-15", "2025-02-28", "2025-12-31", "2024-02-29", "2025-06-30", "2025-03-01", "2025-09-30", "2025-10-01", "2025-01-01", "2025-12-15", "2025-13-01", "2025-00-01", "", "2025-05-01", "2025/05/01", "2025-05-32", "2025-07-15", "2025-06-01", "2025-11-11", "2025-11-12"],
        }

        for i in range(1, 21):
            row: dict[str, str] = {"test_data_id": f"TD-{i:03d}"}
            for field in [f for f in fields if f not in {"test_data_id"}]:
                base_values = examples.get(field, ["valid", "sample", "value", "test", "edge"])
                row[field] = base_values[(i - 1) % len(base_values)]

            if "confirm_password" in row and row.get("password"):
                row["confirm_password"] = row["password"]

            row["expected_result"] = self._expected_result_for_index(i)
            row["test_type"] = self._test_type_for_index(i)

            rows.append(row)

        header = ["test_data_id"] + [field for field in fields if field not in {"test_data_id"}] + ["expected_result", "test_type"]
        csv_buffer = io.StringIO()
        writer = csv.DictWriter(csv_buffer, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in header})

        csv_text = csv_buffer.getvalue().rstrip() + "\n"
        return {
            "header": header,
            "rows": rows,
            "csv_text": csv_text,
        }

    @staticmethod
    def _expected_result_for_index(index: int) -> str:
        if index <= 8:
            return "accepted"
        if index <= 12:
            return "accepted_on_boundary"
        if index <= 16:
            return "rejected_validation_error"
        if index <= 18:
            return "rejected_missing_required_field"
        return "rejected_duplicate_or_workflow_error"

    @staticmethod
    def _test_type_for_index(index: int) -> str:
        if index <= 8:
            return "positive"
        if index <= 12:
            return "boundary"
        if index <= 16:
            return "invalid"
        if index <= 18:
            return "missing_optional"
        return "workflow"

    def _build_coverage_summary(self, rows: list[dict[str, str]]) -> dict[str, Any]:
        counts = {
            "number_of_data_rows": len(rows),
            "positive_cases": sum(1 for row in rows if row.get("test_type") == "positive"),
            "negative_cases": sum(1 for row in rows if row.get("test_type") in {"invalid", "missing_optional", "workflow"}),
            "boundary_cases": sum(1 for row in rows if row.get("test_type") == "boundary"),
            "missing_optional_field_cases": sum(1 for row in rows if row.get("test_type") == "missing_optional"),
            "cross_field_or_workflow_cases": sum(1 for row in rows if row.get("test_type") == "workflow"),
            "important_cases_not_covered_and_why": "The supplied requirement did not include a full application schema, API contract, or domain-specific validation limits; therefore, boundary and workflow cases are intentionally representative rather than exhaustive.",
        }
        return counts
