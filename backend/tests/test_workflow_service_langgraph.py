import os
from pathlib import Path
import pytest
import unittest.mock as mock
from uuid import uuid4

from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase, EvaluationStatus, TestCaseStatus
from backend.services.workflow_service import (
    WorkflowService,
    active_execution_events,
    run_execution_and_stream,
)
from backend.repository.project_repository import JSONProjectRepository
import backend.repository.project_repository as repo_module
from backend.services.llm import LLMService

TEST_STORE_PATH = Path(__file__).parent / "test_project_store.json"


@pytest.fixture(autouse=True)
def setup_test_db():
    if TEST_STORE_PATH.exists():
        os.remove(TEST_STORE_PATH)
    orig_repo = repo_module._active_project_repo
    test_repo = JSONProjectRepository(file_path=TEST_STORE_PATH)
    repo_module._active_project_repo = test_repo
    yield
    repo_module._active_project_repo = orig_repo
    if TEST_STORE_PATH.exists():
        os.remove(TEST_STORE_PATH)



def test_generate_scenarios_via_langgraph(mock_llm_service):
    ws = WorkflowService()
    project = ws.repo.create_project("LangGraph Project", "Testing LangGraph integration")
    req = ws.repo.create_requirement(project.id, "Req Title", "Req Description", "high", "finance")

    scenarios = ws.generate_scenarios(project.id, req.id, count=3)
    assert len(scenarios) == 3
    assert scenarios[0].scenario_name == "Valid Login"
    assert scenarios[0].requirement_id == req.id

    stored = ws.repo.get_scenarios(req.id)
    assert len(stored) == 3


def test_replace_scenarios_preserves_existing_data_when_generation_fails(monkeypatch):
    ws = WorkflowService()
    project = ws.repo.create_project("Scenario Failure Project", "Testing replacement")
    req = ws.repo.create_requirement(project.id, "Req Title", "Req Description", "high", "finance")
    existing = Scenario(
        requirement_id=req.id,
        scenario_name="Existing scenario",
        description="Keep this if generation fails",
    )
    ws.repo.save_scenarios([existing])
    monkeypatch.setattr(
        "backend.graph.workflow.build_graph",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )

    with pytest.raises(RuntimeError, match="model unavailable"):
        ws.generate_scenarios(project.id, req.id, count=3, mode="replace")

    assert ws.repo.get_scenarios(req.id) == [existing]


def test_generate_test_cases_via_langgraph(mock_llm_service):
    ws = WorkflowService()
    project = ws.repo.create_project("LangGraph Project", "Testing LangGraph integration")
    req = ws.repo.create_requirement(project.id, "Req Title", "Req Description", "high", "finance")

    # 1. Save approved scenarios in repository
    sc1 = Scenario(requirement_id=req.id, scenario_name="Valid Login", description="desc", approved=True)
    sc2 = Scenario(requirement_id=req.id, scenario_name="Invalid Login", description="desc", approved=True)
    sc3 = Scenario(requirement_id=req.id, scenario_name="Empty Password", description="desc", approved=True)
    ws.repo.save_scenarios([sc1, sc2, sc3])

    # 2. Generate test cases
    test_cases = ws.generate_test_cases(project.id, req.id)
    # The canned response produces 3 test cases
    assert len(test_cases) == 3
    assert test_cases[0].evaluation_status == EvaluationStatus.NEEDS_REVIEW

    stored = ws.repo.get_test_cases(sc1.id) + ws.repo.get_test_cases(sc2.id) + ws.repo.get_test_cases(sc3.id)
    assert len(stored) == 3


def test_generate_test_cases_preserves_existing_cases_when_generation_fails(mock_llm_service):
    ws = WorkflowService()
    project = ws.repo.create_project("LangGraph Project", "Testing LangGraph integration")
    req = ws.repo.create_requirement(project.id, "Req Title", "Req Description", "high", "finance")
    scenario = Scenario(requirement_id=req.id, scenario_name="Valid Login", description="desc", approved=True)
    ws.repo.save_scenarios([scenario])
    existing_case = TestCase(
        scenario_id=scenario.id,
        title="Existing login test",
        steps=["Submit valid credentials"],
        expected_result="The user is logged in",
    )
    ws.repo.save_test_cases([existing_case])

    with mock.patch("backend.graph.workflow.build_graph", side_effect=RuntimeError("LLM unavailable")):
        with pytest.raises(RuntimeError, match="LLM unavailable"):
            ws.generate_test_cases(project.id, req.id)

    assert ws.repo.get_test_cases(scenario.id) == [existing_case]


def _data_driven_case(ws, *, safe=False):
    project = ws.repo.create_project(
        "LangGraph Project",
        "Testing LangGraph integration",
        target_url="https://coupons.example.test",
    )
    if safe:
        project.is_safe_test_environment = True
        ws.repo.update_project(project)
    req = ws.repo.create_requirement(project.id, "Coupons", "Expired coupons are rejected.", "high", "retail")
    sc = Scenario(
        requirement_id=req.id,
        scenario_name="Expired coupon",
        description="Apply an expired coupon at checkout.",
        path_type="negative",
        approved=True,
    )
    ws.repo.save_scenarios([sc])
    tc = TestCase(
        scenario_id=sc.id,
        title="Checkout rejects an expired coupon",
        steps=["Open the checkout page", "Enter the coupon code 'OLD-2020'", "Apply the coupon"],
        expected_result="The message 'Coupon expired' is shown",
        evaluation_status=EvaluationStatus.APPROVED,
        confidence=0.9,
    )
    ws.repo.save_test_cases([tc])
    return project, tc


def _coupon_datasets():
    from backend.agents.test_data_agent import TestCaseDataBatchResponse, TestCaseDataResponse

    return TestCaseDataBatchResponse(datasets=[
        TestCaseDataResponse(
            test_data={"coupon_code": f"OLD-{index:03d}"},
            targeted_validation_rule="Expired coupons are rejected.",
            test_type="invalid" if index < 18 else "positive",
        )
        for index in range(20)
    ])


class _TemplateLLM:
    """Returns a marker template; tags the one written for the accepted rows."""

    def __init__(self):
        self.prompts = []

    def generate(self, *, system, user, **kwargs):
        self.prompts.append(user)
        tag = "// accepted-template\n" if "accepts the supplied values" in user else ""
        return (
            "import { test, expect } from '@playwright/test';\n"
            "const BASE_URL = process.env.TARGET_URL || 'https://coupons.example.test';\n"
            f"{tag}"
            "test('__TEST_DATA_ID__ [__TEST_TYPE__] coupon', async ({ page }) => {\n"
            "  await page.goto(BASE_URL);\n"
            "  await page.getByLabel('Coupon').fill('__TEST_DATA_01__');\n"
            "  await expect(page.getByText('Coupon expired')).toBeVisible();\n"
            "});"
        )


def test_generate_playwright_script_adds_20_datasets_and_one_test_each():
    ws = WorkflowService()
    project, tc = _data_driven_case(ws)
    llm = _TemplateLLM()

    with mock.patch.object(LLMService, "structured_generate", return_value=_coupon_datasets()), \
         mock.patch.object(LLMService, "generate", side_effect=llm.generate):
        updated_tc = ws._generate_data_driven_script(
            project.id, ws.repo.get_scenario(tc.scenario_id).requirement_id, tc, "Playwright"
        )

    script = updated_tc.playwright_script
    assert len(updated_tc.test_data_sets) == 20
    assert [p["expected_outcome"] for p in updated_tc.test_data_profiles].count("accepted") == 2
    assert script.count("test(") == 20
    assert script.count("test.describe(") == 20
    assert script.count("import { test, expect }") == 1
    assert all(f"OLD-{index:03d}" in script for index in range(20))
    assert all(f"TD-{tc.id.hex[:8].upper()}-{index:02d}" in script for index in range(1, 21))
    # Real steps reach the model, and the two valid rows get their own template.
    assert "Enter the coupon code" in llm.prompts[0]
    assert len(llm.prompts) == 2
    assert script.count("// accepted-template") == 2
    saved = ws.repo.get_test_case(tc.id)
    assert saved.playwright_script == script


def test_generate_playwright_script_uses_read_only_evidence_and_repairs_once():
    evidence = {"pages": [{"url": "https://coupons.example.test/", "forms": [], "elements": []}]}

    class Inspector:
        def __init__(self):
            self.calls = []

        def inspect_project(self, project_id, test_cases, allow_invalid_login_probe):
            self.calls.append(allow_invalid_login_probe)
            return evidence

    inspector = Inspector()
    ws = WorkflowService(inspector=inspector)
    project, tc = _data_driven_case(ws, safe=True)
    llm = _TemplateLLM()
    issues = iter([["TypeScript compilation failed"], []])

    with mock.patch.object(LLMService, "structured_generate", return_value=_coupon_datasets()), \
         mock.patch.object(LLMService, "generate", side_effect=llm.generate), \
         mock.patch(
             "backend.services.workflow_service.validate_playwright_script",
             side_effect=lambda script, dom_context: next(issues),
         ):
        updated_tc = ws._generate_data_driven_script(
            project.id, ws.repo.get_scenario(tc.scenario_id).requirement_id, tc, "Playwright"
        )

    assert inspector.calls == [False]
    assert all("LIVE PAGE EVIDENCE" in prompt for prompt in llm.prompts)
    assert any("REPAIR FEEDBACK" in prompt for prompt in llm.prompts)
    assert updated_tc.playwright_script.count("test(") == 20
    assert updated_tc.script_validation_status == "unverified"


def test_generate_playwright_script_uses_live_verified_generator_for_target_url():
    calls = []

    async def fake_generator(target_url, steps, test_title):
        calls.append((target_url, steps, test_title))
        return "import { test, expect } from '@playwright/test';\ntest('live', async () => {});"

    ws = WorkflowService(script_generator=fake_generator)
    project, tc = _data_driven_case(ws)

    updated_tc = ws.generate_playwright_script(project.id, tc.id)

    assert calls == [("https://coupons.example.test", list(tc.steps), tc.title)]
    assert updated_tc.playwright_script.startswith("import { test, expect }")
    assert updated_tc.script_validation_status == "unverified"
    assert ws.repo.get_test_case(tc.id).playwright_script == updated_tc.playwright_script
    # The live path does not generate datasets.
    assert updated_tc.test_data_sets == []


def test_run_execution_and_stream_via_langgraph(mock_llm_service, fake_playwright_runner):
    ws = WorkflowService()
    project = ws.repo.create_project("LangGraph Project", "Testing")
    req = ws.repo.create_requirement(project.id, "Req", "Desc", "high", "finance")

    sc = Scenario(requirement_id=req.id, scenario_name="Valid Login", description="desc", approved=True)
    ws.repo.save_scenarios([sc])

    tc = TestCase(
        scenario_id=sc.id,
        title="Verify Login",
        steps=["Step 1"],
        expected_result="LoggedIn",
        evaluation_status=EvaluationStatus.APPROVED,
        confidence=0.9,
        playwright_script="await page.goto('/')"
    )
    ws.repo.save_test_cases([tc])

    # Execute and capture event stream
    with mock.patch("backend.agents.execution_agent.PlaywrightRunner", return_value=fake_playwright_runner):
        run_execution_and_stream(ws, project.id, tc.id)

    # Check execution results stored in database
    results = ws.repo.get_execution_results(project.id)
    assert len(results) == 1, active_execution_events[str(tc.id)][-1]
    assert results[0].status.value == "passed"


def test_script_issues_ignore_unobserved_locators_but_catch_placeholders():
    """Live failure: repairing against login-page-only evidence produced
    locator('// TODO: locator for FirstName field') in every test."""
    evidence = {"pages": [{"url": "https://app.example.test/", "forms": [], "elements": []}]}
    unobserved = ["Selector locator '#first-name' is not uniquely observed"]

    with mock.patch(
        "backend.services.workflow_service.validate_playwright_script",
        return_value=unobserved,
    ):
        good = WorkflowService._script_issues("await page.locator('#first-name').fill('A');", evidence)
        bad = WorkflowService._script_issues(
            "await page.locator('// TODO: locator for FirstName field').fill('A');", evidence
        )

    assert good == []
    assert len(bad) == 1 and "TODO" in bad[0]
    # Placeholders are caught even when no page evidence was collected.
    assert WorkflowService._script_issues("page.getByText('TODO confirm text')", None)
    assert WorkflowService._script_issues("page.locator('//button')", None) == []


def test_script_issues_catch_scripts_that_cannot_test_the_app():
    """Live failure: page.locator('getByLabel') timed out in every checkout test."""
    good = (
        "test('checkout', async ({ page }) => {\n"
        "  await page.getByLabel('First Name').fill('A');\n"
        "  await page.locator('#continue').click();\n"
        "  await expect(page.getByText('Thank you')).toBeVisible();\n"
        "});"
    )
    assert WorkflowService._script_issues(good, None) == []

    def issues(snippet):
        return " ".join(WorkflowService._script_issues(snippet, None))

    assert "method name" in issues("await page.locator('getByLabel').fill('A');")
    assert "method name" in issues("await page.locator(\"page.getByRole('button')\").click();")
    assert "selector string" in issues("await page.click('#continue');")
    assert "on purpose" in issues("expect(false).toBeTruthy();")
    assert "on purpose" in issues("test.skip(true, 'locator not observed');")
    assert "waitForTimeout" in issues("await page.waitForTimeout(3000);")
    assert "does not assert" in issues("test('x', async ({ page }) => { await page.goto('/'); });")
