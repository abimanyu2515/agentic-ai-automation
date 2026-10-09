"""
Shared fixtures for backend/tests/. pytest auto-discovers this file for
every test in this directory - anything used by more than one test file
belongs here, not copy-pasted into each one.
"""

import pytest

from backend.agents.evaluation_agent import _EvaluationBatch, _TestCaseEvaluation
from backend.agents.scenario_agent import _GeneratedScenario, _GeneratedScenarioBatch
from backend.agents.test_case_agent import TestCaseListResponse, TestCaseResponse
from backend.services.playwright_runner import PlaywrightRunResult

_CANNED_PLAYWRIGHT_CODE = """import { test, expect } from "@playwright/test";

test("Verify: Valid Login", async ({ page }) => {
  // Step 1: Navigate to the login page
  await page.goto("/login");
  // Step 2: Enter valid credentials - locator is a best guess, no real page to inspect yet
  await page.getByLabel("Username").fill("testuser");
  await page.getByLabel("Password").fill("correct-password");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByText("Welcome")).toBeVisible();
});
"""


class StubLLMService:
    """Test double for LLMService - never touches litellm.

    structured_generate() is keyed by response_model, so one stub can
    serve multiple JSON-based agents in a single graph-level test, each
    expecting a different schema back.

    generate() has no response_model to key off (PlaywrightAgent's output
    is plain code, not a validated schema) - it just returns a single
    canned string, since only one agent currently uses this method."""

    def __init__(self, responses: dict[type, object] | None = None, generate_response: str | None = None):
        self._responses = responses or {}
        self._generate_response = generate_response if generate_response is not None else _CANNED_PLAYWRIGHT_CODE

    def structured_generate(self, *, user, response_model, **kwargs):
        try:
            return self._responses[response_model]
        except KeyError:
            raise AssertionError(
                f"StubLLMService has no canned response for {response_model.__name__} - "
                f"add one when constructing the stub for this test"
            )

    def generate(self, *, system, user, **kwargs) -> str:
        return self._generate_response


class FakePlaywrightRunner:
    """Test double for PlaywrightRunner - never touches npx/subprocess.
    Returns the same canned result for every call by default; construct
    with result_map={run_id: PlaywrightRunResult} for per-test-case
    control when a test needs different outcomes for different scripts."""

    def __init__(self, default_result: PlaywrightRunResult | None = None, result_map: dict[str, PlaywrightRunResult] | None = None):
        self._default = default_result or PlaywrightRunResult(status="passed", duration_seconds=1.5)
        self._result_map = result_map or {}

    def run(self, script: str, run_id: str) -> PlaywrightRunResult:
        return self._result_map.get(run_id, self._default)


@pytest.fixture
def canned_scenario_batch() -> _GeneratedScenarioBatch:
    return _GeneratedScenarioBatch(
        scenarios=[
            _GeneratedScenario(
                scenario_name="Valid Login",
                description="User logs in with correct username and password",
                priority="high",
            ),
            _GeneratedScenario(
                scenario_name="Invalid Login",
                description="User attempts login with an incorrect password",
                priority="medium",
            ),
            _GeneratedScenario(
                scenario_name="Empty Password",
                description="User submits the login form with an empty password field",
                priority="low",
            ),
        ]
    )


def _canned_test_case_batch() -> TestCaseListResponse:
    return TestCaseListResponse(
        test_cases=[
            TestCaseResponse(
                scenario_number=1,
                title="Verify: Valid Login",
                preconditions=["Application is running"],
                steps=["Execute scenario: Valid Login"],
                expected_result="System behaves correctly for 'Valid Login'",
            ),
            TestCaseResponse(
                scenario_number=2,
                title="Verify: Invalid Login",
                preconditions=["Application is running"],
                steps=["Execute scenario: Invalid Login"],
                expected_result="System behaves correctly for 'Invalid Login'",
            ),
            TestCaseResponse(
                scenario_number=3,
                title="Verify: Empty Password",
                preconditions=["Application is running"],
                steps=["Execute scenario: Empty Password"],
                expected_result="System behaves correctly for 'Empty Password'",
            ),
        ]
    )

@pytest.fixture
def canned_evaluation_batch(canned_scenario_batch: _GeneratedScenarioBatch) -> _EvaluationBatch:
    return _EvaluationBatch(
        evaluations=[
            _TestCaseEvaluation(test_case_number=i, relevance=0.9, completeness=0.9, reason="Looks solid")
            for i in range(1, len(canned_scenario_batch.scenarios) + 1)
        ]
    )


@pytest.fixture
def stub_llm_service(
    canned_scenario_batch: _GeneratedScenarioBatch,
    canned_evaluation_batch: _EvaluationBatch,
) -> StubLLMService:
    from backend.agents.requirement_analyst_agent import RequirementEnrichment
    from backend.agents.backlog_creation_agent import UserStoryResponse, EpicResponse, FeatureBlock, StoryBlock
    from backend.agents.qa_story_analyzer_agent import TestingContextResponse, TestStrategyDetails, TestScopeDetails, RiskItem
    from backend.agents.automation_orchestrator_agent import OrchestrationResponse

    return StubLLMService(
        responses={
            _GeneratedScenarioBatch: canned_scenario_batch,
            TestCaseListResponse: _canned_test_case_batch(),
            _EvaluationBatch: canned_evaluation_batch,
            RequirementEnrichment: RequirementEnrichment(
                title="Enriched Requirement Title",
                description="This is an enriched requirement description detailing the full flow.",
                priority="high",
                business_domain="Finance"
            ),
            UserStoryResponse: UserStoryResponse(
                user_story="As a financial user, I want to perform a login so that I can access my accounts.",
                acceptance_criteria=["Verify successful login redirects to dashboard.", "Verify error message on incorrect password."]
            ),
            EpicResponse: EpicResponse(
                title="Canned Epic Title",
                description="Canned Epic Description",
                features=[
                    FeatureBlock(
                        title="Canned Feature Title",
                        description="Canned Feature Description",
                        user_stories=[
                            StoryBlock(
                                title="Canned User Story Title",
                                description="As a user, I want to authenticate, so that I can see the dashboard.",
                                acceptance_criteria=["AC 1", "AC 2"]
                            )
                        ]
                    )
                ]
            ),
            TestingContextResponse: TestingContextResponse(
                risk_analysis=[
                    RiskItem(area="canned risk", risk_level="low", mitigation="canned mitigation")
                ],
                test_strategy=TestStrategyDetails(approach="standard automation", environment="QA"),
                test_scope=TestScopeDetails(in_scope=["auth"], out_scope=[]),
                coverage_suggestions=["verify correct logins"]
            ),
            OrchestrationResponse: OrchestrationResponse(
                test_case_title="Verify: Valid Login",
                execution_type="UI Automation",
                reason="Canned orchestration routing"
            )
        },
        generate_response=_CANNED_PLAYWRIGHT_CODE,
    )


@pytest.fixture
def fake_playwright_runner() -> FakePlaywrightRunner:
    return FakePlaywrightRunner()

@pytest.fixture
def mock_llm_service(stub_llm_service):
    import unittest.mock as mock
    from backend.services.llm import LLMService
    with mock.patch.object(LLMService, "structured_generate", side_effect=stub_llm_service.structured_generate) as m1, \
         mock.patch.object(LLMService, "generate", side_effect=stub_llm_service.generate) as m2:
        yield stub_llm_service