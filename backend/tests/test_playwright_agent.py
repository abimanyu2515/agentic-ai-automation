"""
Two layers, same approach as every other agent:
1. LLMService.generate() <-> litellm - real mock_response. This is the
   FIRST test of generate() at all - every prior agent used
   structured_generate(), so this method has been sitting untested since
   it was written for the ScenarioAgent round.
2. PlaywrightAgent <-> LLMService - stub-based (see conftest.py), proving
   this agent only ever sees approved_test_cases(), never the rejected or
   needs-review ones, and that malformed output is caught rather than
   silently stored.
"""

from uuid import uuid4
from threading import Lock
import time

import pytest

from backend.agents.playwright_agent import PlaywrightAgent
from backend.config.settings import LLMConfig
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus, TestCase
from backend.services.llm import LLMService
from backend.tests.conftest import StubLLMService

_TEST_LLM_CONFIG = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="fake-key-for-test")


def test_llm_service_generate_returns_mock_response_content():
    service = LLMService(config=_TEST_LLM_CONFIG)

    result = service.generate(
        system="irrelevant for this test",
        user="irrelevant - mock_response short-circuits the call",
        mock_response="plain text reply, no JSON involved",
    )

    assert result == "plain text reply, no JSON involved"


def _state_with_mixed_test_cases() -> WorkflowState:
    """One of each outcome, mirroring the doc's running example."""
    requirement = Requirement(title="Profile picture upload", description="Users can upload a JPG under 5MB")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="Valid Upload", description="d")
    approved = TestCase(scenario_id=scenario.id, title="Approved case", steps=["a"], expected_result="e",
                         evaluation_status=EvaluationStatus.APPROVED, confidence=0.95)
    needs_review = TestCase(scenario_id=scenario.id, title="Review case", steps=["a"], expected_result="e",
                             evaluation_status=EvaluationStatus.NEEDS_REVIEW, confidence=0.5)
    rejected = TestCase(scenario_id=scenario.id, title="Rejected case", steps=["a"], expected_result="e",
                         evaluation_status=EvaluationStatus.REJECTED, confidence=0.1)
    state = WorkflowState(requirement=requirement, generated_scenarios=[scenario],
                           generated_test_cases=[approved, needs_review, rejected])
    return state


def test_only_approved_test_cases_get_a_script(stub_llm_service):
    state = _state_with_mixed_test_cases()

    result = PlaywrightAgent(llm_service=stub_llm_service).run(state)

    by_title = {tc.title: tc for tc in result.generated_test_cases}
    assert by_title["Approved case"].playwright_script is not None
    assert by_title["Review case"].playwright_script is None  # never touched - agent doesn't know it exists
    assert by_title["Rejected case"].playwright_script is None


def test_strips_code_fences_from_output(stub_llm_service):
    state = _state_with_mixed_test_cases()

    result = PlaywrightAgent(llm_service=stub_llm_service).run(state)

    script = next(tc for tc in result.generated_test_cases if tc.title == "Approved case").playwright_script
    assert not script.startswith("```")
    assert "test(" in script
    assert "import" in script


def test_playwright_script_generation_uses_extended_llm_timeout():
    class TimeoutCaptureLLMService:
        timeout = None

        def generate(self, *, system, user, **kwargs):
            self.timeout = kwargs.get("timeout")
            return "import { test, expect } from '@playwright/test';\ntest('smoke', async () => {});"

    llm_service = TimeoutCaptureLLMService()
    state = _state_with_mixed_test_cases()

    PlaywrightAgent(llm_service=llm_service).run(state)

    assert llm_service.timeout == 120


def test_script_generation_respects_configured_worker_limit():
    class ConcurrentLLMService:
        def __init__(self):
            self.lock = Lock()
            self.active_calls = 0
            self.max_active_calls = 0

        def generate(self, *, system, user, **kwargs):
            with self.lock:
                self.active_calls += 1
                self.max_active_calls = max(self.max_active_calls, self.active_calls)
            time.sleep(0.02)
            with self.lock:
                self.active_calls -= 1
            return "import { test, expect } from '@playwright/test';\ntest('smoke', async () => {});"

    state = _state_with_mixed_test_cases()
    approved = next(tc for tc in state.generated_test_cases if tc.title == "Approved case")
    state.generated_test_cases.extend(
        TestCase(
            scenario_id=approved.scenario_id,
            title=f"Approved case {index}",
            steps=["a"],
            expected_result="e",
            evaluation_status=EvaluationStatus.APPROVED,
        )
        for index in range(5)
    )
    llm_service = ConcurrentLLMService()

    PlaywrightAgent(llm_service=llm_service, max_workers=3).run(state)

    assert llm_service.max_active_calls == 3
    assert all(
        test_case.playwright_script
        for test_case in state.generated_test_cases
        if test_case.evaluation_status == EvaluationStatus.APPROVED
    )


def test_test_data_template_generates_one_script_and_substitutes_each_case():
    requirement = Requirement(
        title="Registration",
        description="Register a user using the supplied email address.",
    )
    scenarios = [
        Scenario(
            requirement_id=requirement.id,
            scenario_name=f"Test data TD-{index:03d}: {path_type}",
            description="Submit the registration form.",
            path_type=path_type,
            tags=["test_data_agent", f"TD-{index:03d}"],
        )
        for index, path_type in ((1, "positive"), (2, "boundary"))
    ]
    test_cases = [
        TestCase(
            scenario_id=scenarios[0].id,
            title=scenarios[0].scenario_name,
            steps=["Use the email address to register."],
            expected_result="accepted",
            test_data={"email": "first@example.com"},
            evaluation_status=EvaluationStatus.APPROVED,
        ),
        TestCase(
            scenario_id=scenarios[1].id,
            title=scenarios[1].scenario_name,
            steps=["Use the email address to register."],
            expected_result="rejected at boundary",
            test_data={"email": "boundary@example.com"},
            evaluation_status=EvaluationStatus.APPROVED,
        ),
    ]

    class TemplateLLMService:
        def __init__(self):
            self.calls = 0

        def generate(self, *, system, user, **kwargs):
            self.calls += 1
            assert "__TEST_DATA_01__" in user
            return (
                "import { test, expect } from '@playwright/test';\n"
                "test('Test data __TEST_DATA_ID__: __TEST_TYPE__', async ({ page }) => {\n"
                "  await page.fill('#email', '__TEST_DATA_01__');\n"
                "  await expect(page.getByText('__EXPECTED_RESULT__')).toBeVisible();\n"
                "});"
            )

    class TemplateRepository:
        def list_projects(self):
            return []

        def get_scenario(self, scenario_id):
            return next(scenario for scenario in scenarios if scenario.id == scenario_id)

    llm_service = TemplateLLMService()
    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=scenarios,
        generated_test_cases=test_cases,
    )

    result = PlaywrightAgent(
        llm_service=llm_service,
        repo=TemplateRepository(),
        test_data_template=True,
    ).run(state)

    assert llm_service.calls == 1
    scripts = [case.playwright_script for case in result.generated_test_cases]
    assert "Test data TD-001: positive" in scripts[0]
    assert "first@example.com" in scripts[0]
    assert "accepted" in scripts[0]
    assert "Test data TD-002: boundary" in scripts[1]
    assert "boundary@example.com" in scripts[1]
    assert "rejected at boundary" in scripts[1]
    assert all("__TEST_DATA_" not in script for script in scripts)
    assert all("__EXPECTED_RESULT__" not in script for script in scripts)


def test_test_data_template_retries_once_when_model_omits_a_data_marker():
    requirement = Requirement(title="Registration", description="Register a user.")
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Test data TD-001: positive",
        description="Submit the registration form.",
        path_type="positive",
        tags=["test_data_agent", "TD-001"],
    )
    test_case = TestCase(
        scenario_id=scenario.id,
        title=scenario.scenario_name,
        steps=["Use the supplied name and email to register."],
        expected_result="accepted",
        test_data={"full_name": "Alice Johnson", "email": "alice@example.com"},
        evaluation_status=EvaluationStatus.APPROVED,
    )

    class RetryingTemplateLLMService:
        def __init__(self):
            self.calls = 0

        def generate(self, *, system, user, **kwargs):
            self.calls += 1
            data_marker = (
                "  await page.fill('#email', '__TEST_DATA_02__');\n"
                if self.calls > 1 else ""
            )
            return (
                "import { test, expect } from '@playwright/test';\n"
                "test('Test data __TEST_DATA_ID__: __TEST_TYPE__', async ({ page }) => {\n"
                "  await page.fill('#name', '__TEST_DATA_01__');\n"
                f"{data_marker}"
                "  await expect(page.getByText('__EXPECTED_RESULT__')).toBeVisible();\n"
                "});"
            )

    class TemplateRepository:
        def list_projects(self):
            return []

        def get_scenario(self, scenario_id):
            assert scenario_id == scenario.id
            return scenario

    llm_service = RetryingTemplateLLMService()
    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
    )

    result = PlaywrightAgent(
        llm_service=llm_service,
        repo=TemplateRepository(),
        test_data_template=True,
    ).run(state)

    assert llm_service.calls == 2
    assert "Alice Johnson" in result.generated_test_cases[0].playwright_script
    assert "alice@example.com" in result.generated_test_cases[0].playwright_script
    assert any("retrying template generation once" in log for log in result.logs)


def test_test_data_template_expands_selected_case_to_20_distinct_playwright_tests():
    requirement = Requirement(title="Registration", description="Register with a password.")
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Reject numeric-only password",
        description="Reject passwords containing only digits.",
        path_type="negative",
    )
    datasets = [
        {"password": f"{index:08d}", "email": f"user-{index}@example.test"}
        for index in range(20)
    ]
    test_case = TestCase(
        scenario_id=scenario.id,
        title="Invalid Password: numeric-only input",
        steps=["Enter a numeric-only password", "Submit the form"],
        expected_result="Password validation error is shown",
        test_data=datasets[0],
        test_data_sets=datasets,
        evaluation_status=EvaluationStatus.APPROVED,
    )
    script_template = """import { test, expect } from '@playwright/test';
test('Test data __TEST_DATA_ID__: __TEST_TYPE__', async ({ page }) => {
  await page.getByLabel('Password').fill('__TEST_DATA_01__');
  await page.getByLabel('Email').fill('__TEST_DATA_02__');
  await expect(page.getByText('__EXPECTED_RESULT__')).toBeVisible();
});"""
    state = WorkflowState(
        requirement=requirement,
        generated_scenarios=[scenario],
        generated_test_cases=[test_case],
    )

    result = PlaywrightAgent(
        llm_service=StubLLMService(generate_response=script_template),
        repo=type(
            "TemplateRepository",
            (),
            {"list_projects": lambda self: [], "get_scenario": lambda self, scenario_id: scenario},
        )(),
        test_data_template=True,
    ).run(state)

    generated_script = result.generated_test_cases[0].playwright_script
    assert generated_script.count("test(") == 20
    assert generated_script.count("import { test, expect }") == 1
    assert "00000000" in generated_script
    assert "00000019" in generated_script
    assert "user-19@example.test" in generated_script
    assert PlaywrightAgent._escape_js_string_content("O'Brien") == "O\\'Brien"


def test_no_approved_test_cases_is_not_an_error():
    requirement = Requirement(title="x", description="y")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="s", description="d")
    rejected = TestCase(scenario_id=scenario.id, title="Rejected", steps=["a"], expected_result="e",
                         evaluation_status=EvaluationStatus.REJECTED, confidence=0.1)
    state = WorkflowState(requirement=requirement, generated_scenarios=[scenario], generated_test_cases=[rejected])

    result = PlaywrightAgent(llm_service=None).run(state)  # never called - nothing approved to convert

    assert "no approved test cases" in result.logs[-1]


def test_rejects_output_that_does_not_look_like_playwright_code():
    """If the model refuses or explains instead of writing code, that
    must fail loudly, not get stored as if it were a valid script."""
    state = _state_with_mixed_test_cases()
    bad_stub = StubLLMService(generate_response="I cannot generate this test because I don't have enough context.")

    with pytest.raises(ValueError, match="doesn't look like a Playwright test"):
        PlaywrightAgent(llm_service=bad_stub).run(state)


def test_requires_requirement():
    agent = PlaywrightAgent(llm_service=None)  # never called - guard fires first
    state = WorkflowState(requirement=None)

    with pytest.raises(ValueError):
        agent.run(state)