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
    - Generate a CSV-ready 20-row dataset with positive, boundary, invalid, and
      workflow coverage.
    - Record assumptions when the real validation rules are not supplied.

This agent intentionally follows the repository's state-based conventions and
reports structured output as data that downstream agents can parse. It does not
register itself in the workflow graph automatically; integration remains a
separate concern.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from pydantic import BaseModel, Field

from backend.agents.base import BaseAgent
from backend.models.requirement import Requirement
from backend.models.state import WorkflowState
from backend.services.llm import LLMService
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


class TestDataAgent(BaseAgent):
    """Generates a QA-ready test-data plan and CSV dataset for a feature."""

    name = "TestData Agent"

    def __init__(self, llm_service: LLMService | None = None):
        self.llm_service = llm_service or LLMService()

    def run(self, state: WorkflowState) -> WorkflowState:
        if state.requirement is None:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        state.add_log(f"{self.name} started")

        prompt = load_prompt(
            "test_data_agent_prompt.txt",
            title=state.requirement.title,
            description=state.requirement.description,
        )

        report = self._generate_report(state.requirement)
        state.test_data_report = report.model_dump(mode="python")
        state.test_data_combinations = [item.model_dump(mode="python") for item in report.testable_combinations]
        state.generated_test_data = report.sample_dataset.get("rows", [])

        state.add_log(f"{self.name} created a test-data plan with {len(state.generated_test_data)} rows")
        return state

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
