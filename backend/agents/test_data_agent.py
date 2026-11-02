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
from collections import Counter
import csv
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import hashlib
import io
import json
import re
from uuid import uuid4
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

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


TEST_TYPE_ALIASES = {
    "valid": "positive",
    "happy_path": "positive",
    "end_to_end": "positive",
    "e2e": "positive",
    "negative": "invalid",
    "edge": "boundary",
    "edge_case": "boundary",
    "missing": "missing_required",
    "required": "missing_required",
}

# Words in a happy-path case's name, title, or expected result that show it
# is really about a failure.
NEGATIVE_CASE_MARKERS = (
    "invalid", "reject", "error", "empty", "blank", "missing",
    "fail", "incorrect", "wrong", "not allowed", "denied",
)
POSITIVE_CASE_INTENT = (
    "This is a positive (happy-path) case. All 20 datasets must be valid, "
    "complete journeys that reach the expected result: test_type \"positive\", "
    "expected_outcome \"accepted\", and no empty field. Make them differ by "
    "using different valid values, item choices, and item counts, never by "
    "leaving a field blank or making a value invalid."
)
NEGATIVE_CASE_INTENT = (
    "This case checks a failure, boundary, or edge behavior. Every dataset must "
    "trigger the behavior named in the selected test case."
)

# Row columns the agent adds itself; they are never application inputs.
CASE_METADATA_FIELDS = frozenset({
    "test_data_id",
    "targeted_validation_rule",
    "expected_result",
    "test_type",
    "expected_outcome",
})

# How many LLM requests the per-case path makes before giving up.
CASE_DATA_REQUESTS = 6

# Most earlier datasets shown to the model as values to avoid. Bounds the
# prompt for local models with small context windows.
MAX_PROMPT_RESERVED_DATASETS = 60

# How many times the field plan is requested when it leaves input steps
# without fields.
FIELD_PLAN_REQUESTS = 2

# Steps that make the tester type, pick, or add something, so they need at
# least one field. Credential steps are left out: credentials come from the
# vault, never from test data.
INPUT_STEP_PATTERN = re.compile(
    r"\b(enter|enters|type|types|fill|fills|input|select|selects|choose|chooses|"
    r"pick|picks|add|adds|upload|uploads|provide|provides|search|searches|"
    r"apply|applies|set|sets|change|changes|update|updates|sort|sorts|"
    r"filter|filters)\b"
)
CREDENTIAL_STEP_PATTERN = re.compile(
    r"\b(log ?in|logs ?in|sign ?in|signs ?in|username|password|credentials?)\b"
)
# "Log in with valid credentials" enters credentials without an input verb.
CREDENTIAL_ENTRY_PATTERN = re.compile(r"\b(log ?in|logs ?in|sign ?in|signs ?in) (with|as)\b")
# Field names that hold an existing account's login. Those values come from
# the vault, so made-up ones would make the script fail to log in.
CREDENTIAL_FIELD_PATTERN = re.compile(
    r"((login|account|existing|valid|user)_)?"
    r"(user_?name|user_?id|login|password|passwd|pwd|pass)"
)
# expected_ fields the dataset itself determines, such as a cart count. Other
# expected_ values (prices, taxes, totals) were guessed in live runs; the
# script reads those from the page instead.
COUNT_FIELD_PATTERN = re.compile(r"count|quantity|number_of|num_|items")
LIST_FIELD_PATTERN = re.compile(r"product|item")
# Steps or expected results that check money. Such a case gets the app's price
# list once, and the agent computes each row's totals from it.
PRICE_CHECK_PATTERN = re.compile(r"\b(price|prices|total|totals|subtotal|tax|taxes|amount)\b")
CENT = Decimal("0.01")

# A flow that creates its own account needs that account's new credentials.
ACCOUNT_CREATION_PATTERN = re.compile(
    r"\b(register|registers|registration|sign ?up|signs ?up|"
    r"creates? (a |an )?(new )?account)\b"
)

# A valid login uses the project's one vault account. Its datasets hold these
# references instead of credentials; PlaywrightAgent resolves them to
# process.env.TARGET_USERNAME / TARGET_PASSWORD when the test runs. A
# " run NN" suffix keeps the 20 rows of a login-only case distinct.
VAULT_USERNAME = "{{vault:TARGET_USERNAME}}"
VAULT_PASSWORD = "{{vault:TARGET_PASSWORD}}"
VAULT_LOGIN_RULE = "The project's vault credentials are accepted."

# Cases with more steps than this, or with these words, describe a flow across
# several screens. Their data must cover every step, which the single-field
# local generators cannot do.
SINGLE_FORM_MAX_STEPS = 4
END_TO_END_MARKERS = (
    "end to end", "end-to-end", "e2e", "journey", "full flow",
    "complete flow", "complete process", "from start to finish",
)


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
    # Whether the application should accept or reject this dataset. Empty
    # means "same outcome as the selected test case's expected result".
    expected_outcome: Literal["accepted", "rejected", ""] = ""
    assumptions: list[str] = Field(default_factory=list)

    @field_validator("test_data", mode="before")
    @classmethod
    def _stringify_values(cls, value):
        # Models send zip codes and amounts as JSON numbers and blanks as null.
        if not isinstance(value, dict):
            return value
        return {
            str(key): "" if item is None else str(item)
            for key, item in value.items()
        }

    @field_validator("test_type", mode="before")
    @classmethod
    def _normalize_test_type(cls, value):
        if not isinstance(value, str):
            return value
        label = re.sub(r"[\s-]+", "_", value.strip().lower())
        return TEST_TYPE_ALIASES.get(label, label)

    @field_validator("expected_outcome", mode="before")
    @classmethod
    def _normalize_outcome(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _default_outcome(self) -> "TestCaseDataResponse":
        if not self.expected_outcome:
            if self.test_type == "positive":
                self.expected_outcome = "accepted"
            elif self.test_type in {"invalid", "missing_required"}:
                self.expected_outcome = "rejected"
        return self


class TestCaseDataBatchResponse(BaseModel):
    """Datasets for the same selected test case. The prompt asks for 20; the
    agent collects unique ones across replies until it has 20."""

    # Every input the test needs from its first step to its last. Every
    # dataset must carry all of them.
    required_fields: list[str] = Field(default_factory=list)
    datasets: list[TestCaseDataResponse] = Field(min_length=1)

    @field_validator("datasets", mode="before")
    @classmethod
    def _drop_malformed_items(cls, value):
        # Seen live: a model returned 44 entries, some of them bare numbers.
        # Malformed entries say nothing about the well-formed ones, so drop
        # them here and let the agent collect unique datasets from the rest.
        if not isinstance(value, list):
            return value
        kept = []
        for item in cls._unwrap(value):
            try:
                kept.append(TestCaseDataResponse.model_validate(item))
            except ValidationError:
                continue
        return kept

    @classmethod
    def _unwrap(cls, items: list) -> list:
        """Flatten nested lists and decode entries sent as JSON text,
        including several objects joined into one string."""
        flat = []
        for item in items:
            if isinstance(item, list):
                flat.extend(cls._unwrap(item))
            elif isinstance(item, str):
                for text in (item, f"[{item}]"):
                    try:
                        decoded = json.loads(text)
                    except ValueError:
                        continue
                    flat.extend(cls._unwrap(
                        decoded if isinstance(decoded, list) else [decoded]
                    ))
                    break
            else:
                flat.append(item)
        return flat


def _snake_case(name: str) -> str:
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(name).strip())
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _split_field_text(value):
    if isinstance(value, str):
        return [item for item in re.split(r"[,;]", value) if item.strip()]
    return value


class FieldPlanStep(BaseModel):
    """Inputs one step of the test case needs."""

    step_number: int = Field(ge=1)
    fields: list[str] = Field(default_factory=list)

    _split_text = field_validator("fields", mode="before")(_split_field_text)


class TestCaseFieldPlan(BaseModel):
    """Every input the test case needs, step by step, before any data is made."""

    precondition_fields: list[str] = Field(default_factory=list)
    steps: list[FieldPlanStep] = Field(default_factory=list)

    _split_text = field_validator("precondition_fields", mode="before")(_split_field_text)

    def step_fields(self) -> dict[int, list[str]]:
        planned: dict[int, list[str]] = {}
        for step in self.steps:
            planned.setdefault(step.step_number, []).extend(
                name for name in map(_snake_case, step.fields) if name
            )
        return planned

    def field_names(self) -> tuple[str, ...]:
        """All planned fields in the order the flow uses them, without repeats."""
        ordered = [_snake_case(name) for name in self.precondition_fields]
        for _, fields in sorted(self.step_fields().items()):
            ordered.extend(fields)
        return tuple(dict.fromkeys(name for name in ordered if name))


def _to_decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    percent = text.endswith("%")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number / 100 if percent else number


class CatalogProduct(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0)

    @field_validator("price", mode="before")
    @classmethod
    def _parse_price(cls, value):
        return _to_decimal(value)


class PriceCatalog(BaseModel):
    """The products a case can pick, with unit prices, and the tax rate."""

    products: list[CatalogProduct] = Field(default_factory=list)
    tax_rate: Decimal | None = Field(default=None, ge=0)
    source: str = ""

    @field_validator("tax_rate", mode="before")
    @classmethod
    def _parse_rate(cls, value):
        rate = _to_decimal(value)
        # "8" means 8%, never 800%.
        return rate / 100 if rate is not None and rate > 1 else rate

    def price_of(self) -> dict[str, tuple[str, Decimal]]:
        """Lowercase name -> (exact name, unit price)."""
        return {
            product.name.strip().lower(): (product.name.strip(), product.price)
            for product in self.products
        }


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
        reserved_inputs = [
            {
                str(key): str(value)
                for key, value in dataset.items()
                if key not in CASE_METADATA_FIELDS
            }
            for dataset in reserved_datasets
            if isinstance(dataset, dict) and dataset
        ]
        validation_rules = [
            *requirement.functional_requirements,
            *requirement.business_rules,
            *requirement.acceptance_criteria,
        ]
        source_test_data = self._scenario_test_data(scenario)
        source_test_data_for_prompt = {
            key: value
            for key, value in (test_case.test_data or source_test_data).items()
            if key not in CASE_METADATA_FIELDS
        }
        positive_only = self._is_positive_case(scenario, test_case)
        application_context = context.get("application_context", {})
        if not isinstance(application_context, dict):
            raise ValueError(f"{self.name} received invalid application context")
        prompt_context = {
            "requirement_title": requirement.title,
            "requirement_description": requirement.description,
            "application_context": json.dumps(application_context, ensure_ascii=False),
            "validation_rules": json.dumps(validation_rules, ensure_ascii=False),
            "scenario": json.dumps(
                {
                    "name": scenario.scenario_name,
                    "description": scenario.description,
                },
                ensure_ascii=False,
            ),
            "test_case": json.dumps(
                {
                    "id": str(test_case.id),
                    "title": test_case.title,
                    "preconditions": test_case.preconditions,
                    "steps": test_case.steps,
                    "expected_result": test_case.expected_result,
                    "approved_test_data": source_test_data_for_prompt,
                },
                ensure_ascii=False,
            ),
        }
        enters_credentials = any(
            self._enters_credentials(step) for step in test_case.steps
        )
        creates_account = any(
            ACCOUNT_CREATION_PATTERN.search(text.lower())
            for text in [*test_case.preconditions, *test_case.steps]
        )
        # A positive case that logs in uses the vault account: its credential
        # fields hold vault references, never invented "valid" accounts (seen
        # live: 19 made-up accounts, including locked_out_user).
        vault_fields = (
            self._credential_field_names(source_test_data_for_prompt)
            if positive_only and enters_credentials and not creates_account
            else None
        )
        # Model-written credentials are kept only where they are the test
        # data: an invalid login, or a flow that registers its own account.
        # Otherwise login comes from the vault, as in a "User is logged in"
        # precondition.
        keep_credentials = creates_account or (enters_credentials and vault_fields is None)
        if vault_fields is not None and self._is_login_only_case(test_case):
            generated_datasets = self._vault_login_datasets(vault_fields)
        else:
            generated_datasets = self._generate_deterministic_case_data(
                scenario=scenario,
                test_case=test_case,
                source_test_data=source_test_data,
                reserved_fingerprints=reserved_fingerprints,
            )
        # Fix every field the flow needs, step by step, before asking for
        # values. One reply that both plans and fills 20 rows tends to stop
        # at the first form (seen with small local models).
        field_plan = (
            self._plan_fields(state, test_case, prompt_context)
            if generated_datasets is None else None
        )
        # Seen live: a model guessed a different tax rate on every row. Ask for
        # the price list once and compute each row's totals from it instead.
        price_catalog = (
            self._price_catalog(state, prompt_context)
            if generated_datasets is None and positive_only
            and any(
                PRICE_CHECK_PATTERN.search(text.lower())
                for text in [*test_case.steps, test_case.expected_result]
            )
            else None
        )
        # Models rarely hit an exact count in one long reply (seen live: 44,
        # then 18). Collect unique datasets across attempts until there are 20,
        # instead of requiring any single reply to be exactly right.
        collected: list[TestCaseDataResponse] = []
        accepted_fingerprints: set[str] = set()
        field_names: tuple[str, ...] | None = None
        if field_plan is not None:
            field_names = tuple(
                self._clean_inputs(
                    dict.fromkeys(field_plan.field_names(), "x"), keep_credentials
                )
            ) or None
        last_llm_error: LLMServiceError | None = None
        for _ in (range(CASE_DATA_REQUESTS) if generated_datasets is None else []):
            user_prompt = load_prompt(
                "test_case_data_prompt.txt",
                **prompt_context,
                # The model cannot invert a hash, so show the values themselves:
                # this case's accepted rows first, then other cases' rows.
                reserved_datasets=json.dumps(
                    [
                        *(dataset.test_data for dataset in collected),
                        *reserved_inputs,
                    ][:MAX_PROMPT_RESERVED_DATASETS],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                uniqueness_nonce=uuid4().hex,
                case_intent=(
                    POSITIVE_CASE_INTENT if positive_only else NEGATIVE_CASE_INTENT
                ),
            )
            if field_plan is not None:
                user_prompt += "\n\n" + self._describe_field_plan(field_plan, test_case)
            if price_catalog is not None:
                user_prompt += (
                    "\n\nProducts you may choose (use these exact names; the platform "
                    "computes prices, tax, and totals itself, so add no expected_ price, "
                    "tax, or total fields): "
                    + "; ".join(product.name for product in price_catalog.products)
                )
            if collected:
                # Follow-up request: ask only for what is still missing.
                user_prompt += (
                    f"\n\nFollow-up request: {len(collected)} datasets are already "
                    f"accepted. Return exactly {20 - len(collected)} NEW datasets."
                )
            if field_names is not None:
                user_prompt += (
                    "\n\nEvery dataset must use exactly these field names, in this order: "
                    f"{json.dumps(list(field_names), ensure_ascii=False)}"
                )
            try:
                candidate = self.llm_service.structured_generate(
                    user=user_prompt,
                    response_model=TestCaseDataBatchResponse,
                    max_retries=1,
                    timeout=240,
                )
            except LLMServiceError as exc:
                # The deterministic generator already returned None for these
                # inputs before the loop, so the only useful recovery is
                # another LLM attempt with a fresh nonce.
                last_llm_error = exc
                state.add_log(f"{self.name}: LLM test-data attempt failed: {exc}")
                continue
            last_llm_error = None
            for dataset in candidate.datasets:
                if field_plan is not None:
                    # Planned names are snake_case; accept "Card Number" for card_number.
                    dataset.test_data = {
                        _snake_case(key): value
                        for key, value in dataset.test_data.items()
                    }
                dataset.test_data = self._clean_inputs(
                    dataset.test_data, keep_credentials
                )
            usable = [
                dataset for dataset in candidate.datasets
                if dataset.test_data
                and all(key.strip() for key in dataset.test_data)
                and (not positive_only or self._is_complete_positive(dataset))
                and self._counts_match(dataset.test_data)
                and (
                    price_catalog is None
                    or self._use_catalog_names(dataset, price_catalog)
                )
            ]
            if not usable:
                continue
            # All 20 rows must share one field set. The first reply with usable
            # data fixes it: the most common field set among the datasets that
            # carry every field the model said the flow needs, so a row that
            # skips a later step's inputs cannot set the shape.
            if field_names is None:
                field_names = self._choose_field_names(
                    usable, candidate.required_fields
                )
            for dataset in usable:
                fingerprint = self._dataset_fingerprint(dataset.test_data)
                if (
                    frozenset(dataset.test_data) != frozenset(field_names)
                    or fingerprint in accepted_fingerprints
                    or fingerprint in reserved_fingerprints
                ):
                    continue
                accepted_fingerprints.add(fingerprint)
                collected.append(dataset)
            state.add_log(
                f"{self.name}: collected {min(len(collected), 20)} of 20 unique datasets"
            )
            if len(collected) >= 20:
                generated_datasets = collected[:20]
                if price_catalog is not None:
                    for dataset in generated_datasets:
                        self._add_price_fields(dataset, price_catalog)
                if vault_fields is not None:
                    # Login happens first, so its fields lead the row.
                    for dataset in generated_datasets:
                        dataset.test_data = {
                            vault_fields[0]: VAULT_USERNAME,
                            vault_fields[1]: VAULT_PASSWORD,
                            **dataset.test_data,
                        }
                break

        if generated_datasets is None:
            reason = f"; last error: {last_llm_error}" if last_llm_error else ""
            raise ValueError(
                f"{self.name} could not generate 20 unique datasets for "
                f"'{test_case.title}' after {CASE_DATA_REQUESTS} requests "
                f"(collected {len(collected)}){reason}. Please try again."
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
                "expected_outcome": dataset.expected_outcome,
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
        test_case.test_data_profiles = [
            {
                "test_type": dataset.test_type,
                "expected_outcome": dataset.expected_outcome,
                "targeted_validation_rule": dataset.targeted_validation_rule,
            }
            for dataset in generated_datasets
        ]
        state.add_log(
            f"{self.name} generated {len(input_datasets)} unique datasets "
            f"for test case '{test_case.title}'"
        )
        return state

    @classmethod
    def _generate_deterministic_case_data(
        cls,
        *,
        scenario: Scenario,
        test_case: TestCase,
        source_test_data: dict[str, str],
        reserved_fingerprints: set[str],
    ) -> list[TestCaseDataResponse] | None:
        base_data = {
            key: value
            for key, value in (test_case.test_data or source_test_data).items()
            if key not in CASE_METADATA_FIELDS
        }
        # The local generators vary one field of one form. A flow across
        # several screens needs inputs for every step, so leave it to the LLM.
        case_text = " ".join(
            [scenario.scenario_name, test_case.title, *test_case.steps]
        ).lower()
        if len(test_case.steps) > SINGLE_FORM_MAX_STEPS or any(
            marker in case_text for marker in END_TO_END_MARKERS
        ):
            return None
        # Only the selected case decides whether this is a negative test. The
        # requirement mentions "invalid" or "reject" for its other cases too.
        narrative = " ".join(
            [
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
            # A case that names another field (an invalid card number, say)
            # is about that field, not the password.
            and not any(
                field.lower().replace("_", " ") in case_focus
                for field in base_data
                if field != password_field
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

    def _plan_fields(
        self,
        state: WorkflowState,
        test_case: TestCase,
        prompt_context: dict[str, str],
    ) -> TestCaseFieldPlan | None:
        """Ask for the inputs of every step, and ask again while a step that
        takes input has none. Returns None when no usable plan comes back, so
        the data request plans the fields itself as before."""
        best: tuple[TestCaseFieldPlan, list[int]] | None = None
        feedback = ""
        for _ in range(FIELD_PLAN_REQUESTS):
            try:
                plan = self.llm_service.structured_generate(
                    user=load_prompt("test_case_field_plan_prompt.txt", **prompt_context)
                    + feedback,
                    response_model=TestCaseFieldPlan,
                    max_retries=1,
                    timeout=120,
                )
            except LLMServiceError as exc:
                state.add_log(f"{self.name}: field plan attempt failed: {exc}")
                continue
            if not isinstance(plan, TestCaseFieldPlan) or not plan.field_names():
                continue
            missing = self._steps_without_fields(plan, test_case)
            if best is None or len(missing) < len(best[1]):
                best = (plan, missing)
            if not missing:
                break
            feedback = (
                "\n\nYour previous plan gave no fields to these steps, although "
                "each one has the tester enter, choose, or add something: "
                + "; ".join(
                    f"step {number}: {test_case.steps[number - 1]}" for number in missing
                )
                + ". Add the fields those steps need, and keep the fields of the other steps."
            )
        if best is None:
            state.add_log(f"{self.name}: no field plan; the data request plans fields itself")
            return None
        plan, missing = best
        if missing:
            state.add_log(
                f"{self.name}: field plan still has no fields for steps "
                f"{', '.join(map(str, missing))}"
            )
        state.add_log(f"{self.name}: planned fields {', '.join(plan.field_names())}")
        return plan

    def _price_catalog(
        self,
        state: WorkflowState,
        prompt_context: dict[str, str],
    ) -> PriceCatalog | None:
        """The app's products and prices, or None when they are not known, in
        which case rows carry no price fields rather than guessed ones."""
        try:
            catalog = self.llm_service.structured_generate(
                user=load_prompt("test_case_price_catalog_prompt.txt", **prompt_context),
                response_model=PriceCatalog,
                max_retries=1,
                timeout=120,
            )
        except LLMServiceError as exc:
            state.add_log(f"{self.name}: price list request failed: {exc}")
            return None
        if not isinstance(catalog, PriceCatalog) or not catalog.products:
            state.add_log(f"{self.name}: no known price list; totals are not added")
            return None
        state.add_log(
            f"{self.name}: price list of {len(catalog.products)} products"
            f" ({catalog.source or 'unstated source'}), tax rate "
            f"{catalog.tax_rate if catalog.tax_rate is not None else 'unknown'}"
        )
        return catalog

    @staticmethod
    def _product_field(test_data: dict[str, str]) -> str | None:
        fields = [
            key for key in test_data
            if not key.startswith("expected_") and LIST_FIELD_PATTERN.search(key.lower())
        ]
        return fields[0] if len(fields) == 1 else None

    @classmethod
    def _use_catalog_names(cls, dataset: "TestCaseDataResponse", catalog: PriceCatalog) -> bool:
        """Every chosen product must be in the price list; names are rewritten
        to the list's exact spelling."""
        field = cls._product_field(dataset.test_data)
        if field is None:
            return True
        price_of = catalog.price_of()
        names = []
        for item in dataset.test_data[field].split(";"):
            if not item.strip():
                continue
            match = price_of.get(item.strip().lower())
            if match is None:
                return False
            names.append(match[0])
        dataset.test_data[field] = "; ".join(names)
        return True

    @classmethod
    def _add_price_fields(cls, dataset: "TestCaseDataResponse", catalog: PriceCatalog) -> None:
        field = cls._product_field(dataset.test_data)
        # With quantities, line totals depend on them; leave totals out.
        if field is None or any("quantit" in key.lower() for key in dataset.test_data):
            return
        price_of = catalog.price_of()
        item_total = sum(
            (price_of[item.strip().lower()][1] for item in dataset.test_data[field].split(";") if item.strip()),
            Decimal("0"),
        ).quantize(CENT, ROUND_HALF_UP)
        prices = {"expected_item_total": item_total}
        if catalog.tax_rate is not None:
            tax = (item_total * catalog.tax_rate).quantize(CENT, ROUND_HALF_UP)
            prices["expected_tax"] = tax
            prices["expected_total"] = item_total + tax
        dataset.test_data.update({key: f"{value:.2f}" for key, value in prices.items()})
        dataset.assumptions = [
            *dataset.assumptions,
            "Unit prices and tax rate come from "
            f"{catalog.source or 'the price list'}; the agent computed the totals.",
        ]

    @staticmethod
    def _enters_credentials(step: str) -> bool:
        step = step.lower()
        return bool(CREDENTIAL_STEP_PATTERN.search(step)) and bool(
            INPUT_STEP_PATTERN.search(step) or CREDENTIAL_ENTRY_PATTERN.search(step)
        )

    @staticmethod
    def _is_login_only_case(test_case: TestCase) -> bool:
        """No step takes input other than the account's credentials."""
        return not any(
            INPUT_STEP_PATTERN.search(step.lower())
            and not CREDENTIAL_STEP_PATTERN.search(step.lower())
            for step in test_case.steps
        )

    @staticmethod
    def _credential_field_names(approved_test_data: dict[str, str]) -> tuple[str, str]:
        """The case's own username and password field names, if it has them."""
        username = next(
            (
                key for key in approved_test_data
                if CREDENTIAL_FIELD_PATTERN.fullmatch(_snake_case(key))
                and "pass" not in key.lower() and "pwd" not in key.lower()
            ),
            "username",
        )
        password = next(
            (
                key for key in approved_test_data
                if CREDENTIAL_FIELD_PATTERN.fullmatch(_snake_case(key))
                and ("pass" in key.lower() or "pwd" in key.lower())
            ),
            "password",
        )
        return username, password

    @staticmethod
    def _vault_login_datasets(fields: tuple[str, str]) -> list[TestCaseDataResponse]:
        """20 logins with the vault account. One account gives one valid login,
        so the rows differ only by run number and repeat that login."""
        return [
            TestCaseDataResponse(
                test_data={
                    fields[0]: f"{VAULT_USERNAME} run {index:02d}",
                    fields[1]: f"{VAULT_PASSWORD} run {index:02d}",
                },
                targeted_validation_rule=VAULT_LOGIN_RULE,
                test_type="positive",
                expected_outcome="accepted",
                assumptions=[
                    "Every row logs in with the project's vault account "
                    "(TARGET_USERNAME and TARGET_PASSWORD), the only account known "
                    "to be valid; the run number keeps the 20 rows distinct."
                ],
            )
            for index in range(1, 21)
        ]

    @staticmethod
    def _clean_inputs(test_data: dict[str, str], keep_credentials: bool) -> dict[str, str]:
        """Drop fields whose values a model can only guess: an existing
        account's credentials, and expected_ values other than counts."""
        cleaned = {}
        for key, value in test_data.items():
            name = _snake_case(key)
            if not keep_credentials and CREDENTIAL_FIELD_PATTERN.fullmatch(name):
                continue
            if name.startswith("expected_") and not COUNT_FIELD_PATTERN.search(name):
                continue
            cleaned[key] = value
        return cleaned

    @staticmethod
    def _counts_match(test_data: dict[str, str]) -> bool:
        """An expected count must equal the items listed in the same dataset."""
        list_fields = [
            key for key in test_data
            if not key.startswith("expected_") and LIST_FIELD_PATTERN.search(key.lower())
        ]
        # With quantities, a cart count may sum them; only check plain lists.
        if len(list_fields) != 1 or any("quantit" in key.lower() for key in test_data):
            return True
        items = [item for item in test_data[list_fields[0]].split(";") if item.strip()]
        for key, value in test_data.items():
            if key.startswith("expected_") and COUNT_FIELD_PATTERN.search(key.lower()):
                if not re.fullmatch(r"\s*\d+\s*", value) or int(value) != len(items):
                    return False
        return True

    @staticmethod
    def _steps_without_fields(plan: TestCaseFieldPlan, test_case: TestCase) -> list[int]:
        planned = plan.step_fields()
        return [
            number
            for number, step in enumerate(test_case.steps, start=1)
            if INPUT_STEP_PATTERN.search(step.lower())
            and not CREDENTIAL_STEP_PATTERN.search(step.lower())
            and not planned.get(number)
        ]

    @staticmethod
    def _describe_field_plan(plan: TestCaseFieldPlan, test_case: TestCase) -> str:
        lines = ["Field plan (every dataset fills every field listed here):"]
        preconditions = [
            name for name in map(_snake_case, plan.precondition_fields) if name
        ]
        if preconditions:
            lines.append(f"- Preconditions: {', '.join(preconditions)}")
        for number, fields in sorted(plan.step_fields().items()):
            if fields and number <= len(test_case.steps):
                lines.append(
                    f"- Step {number} ({test_case.steps[number - 1]}): {', '.join(fields)}"
                )
        return "\n".join(lines)

    @staticmethod
    def _is_positive_case(scenario: Scenario, test_case: TestCase) -> bool:
        """A happy-path case that names no failure: every dataset must take
        the flow to its successful end."""
        if scenario.path_type != "happy_path":
            return False
        case_text = " ".join(
            [scenario.scenario_name, test_case.title, test_case.expected_result]
        ).lower()
        return not any(marker in case_text for marker in NEGATIVE_CASE_MARKERS)

    @staticmethod
    def _is_complete_positive(dataset: TestCaseDataResponse) -> bool:
        # Seen live: a valid checkout case got rows with a blank first or
        # last name, which can never reach the confirmation page.
        return (
            dataset.test_type in {"positive", "workflow"}
            and dataset.expected_outcome == "accepted"
            and all(value.strip() for value in dataset.test_data.values())
        )

    @staticmethod
    def _choose_field_names(
        datasets: list[TestCaseDataResponse],
        required_fields: list[str],
    ) -> tuple[str, ...]:
        required = {field.strip() for field in required_fields if field.strip()}
        complete = [
            dataset for dataset in datasets
            if required <= set(dataset.test_data)
        ] or datasets
        chosen = Counter(
            frozenset(dataset.test_data) for dataset in complete
        ).most_common(1)[0][0]
        # Keep the model's field order, which follows the steps of the flow.
        first = next(
            dataset for dataset in complete
            if frozenset(dataset.test_data) == chosen
        )
        return tuple(first.test_data)

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
