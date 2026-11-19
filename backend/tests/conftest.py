"""
Shared fixtures for backend/tests/. pytest auto-discovers this file for
every test in this directory - anything used by more than one test file
belongs here, not copy-pasted into each one.
"""

import pytest

from backend.repository.token_usage_store import InMemoryTokenUsageStore
from backend.services.token_usage_service import TokenUsageService, set_token_usage_service

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
        self.calls: list[dict] = []  # the keyword arguments of each run, e.g. target_url

    def run(
        self,
        script: str,
        run_id: str,
        project_id: str | None = None,
        **kwargs,
    ) -> PlaywrightRunResult:
        self.calls.append({"run_id": run_id, "project_id": project_id, **kwargs})
        return self._result_map.get(run_id, self._default)


@pytest.fixture
def canned_scenario_batch() -> _GeneratedScenarioBatch:
    return _GeneratedScenarioBatch(
        scenarios=[
            _GeneratedScenario(
                scenario_name="Valid Login",
                description="User logs in with correct username and password",
                priority="high",
                path_type="happy_path",
            ),
            _GeneratedScenario(
                scenario_name="Invalid Login",
                description="User attempts login with an incorrect password",
                priority="medium",
                path_type="negative",
            ),
            _GeneratedScenario(
                scenario_name="Empty Password",
                description="User submits the login form with an empty password field",
                priority="low",
                path_type="edge_case",
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

@pytest.fixture(autouse=True)
def token_usage_service(tmp_path):
    """Every test records LLM token usage into memory, never into
    backend/database/token_usage.jsonl. Tests that care read it from here."""
    service = TokenUsageService(
        store=InMemoryTokenUsageStore(),
        settings_path=tmp_path / "token_usage_settings.json",
        async_writes=False,
    )
    set_token_usage_service(service)
    yield service
    set_token_usage_service(None)


# ---------------------------------------------------------------- spec 0009: users, roles, and secrets

TEST_WORKSPACE_SECRET = "test-workspace-secret"
TEST_JIRA_SECRET = "test-jira-secret"


@pytest.fixture(autouse=True)
def webhook_secrets():
    """Both webhook secrets are set for every test, so the machine routes
    behave as configured. Tests of the unset case clear one with monkeypatch.
    This restores by hand on purpose: an autouse fixture that requests
    `monkeypatch` makes it set up first and tear down last, which undoes
    other fixtures' restores (for example a swapped project repository)."""
    from backend.config.settings import get_settings

    webhooks = get_settings().webhooks
    saved = (webhooks.workspace_secret, webhooks.jira_secret)
    webhooks.workspace_secret, webhooks.jira_secret = TEST_WORKSPACE_SECRET, TEST_JIRA_SECRET
    yield webhooks
    webhooks.workspace_secret, webhooks.jira_secret = saved


@pytest.fixture
def webhook_headers():
    """`webhook_headers()` for the workspace, `webhook_headers("jira")` for Jira."""
    def headers(caller: str = "workspace") -> dict[str, str]:
        secret = TEST_JIRA_SECRET if caller == "jira" else TEST_WORKSPACE_SECRET
        return {"X-Webhook-Secret": secret}
    return headers


@pytest.fixture(scope="session")
def rbac_db():
    """An isolated SQLite database with only the auth tables, seeded with the
    real roles and permissions. Session scope: seeding hashes the dev
    passwords, which is slow. Tests only add users with unique emails."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import backend.database.db_models  # noqa: F401  (registers the tables)
    from backend.database.db import Base
    from backend.database.db_seeder import seed_database

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as conn:
        # Users are assigned to JSON store projects that have no projects row.
        conn.execute(text("PRAGMA foreign_keys=OFF"))
    auth_tables = ["users", "roles", "permissions", "role_permissions", "project_users"]
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in auth_tables])
    session = sessionmaker(bind=engine)()
    seed_database(session)
    yield session
    session.close()
    engine.dispose()


class SignedInUser:
    def __init__(self, user, token: str):
        self.user = user
        self.id = user.id
        self.role = user.role
        self.headers = {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sign_in(rbac_db):
    """`sign_in(role, projects=[...], project_role=None)` creates a user with
    that global role, assigns it to each project (as `project_role`, default
    the same role), points the app at the seeded RBAC database, and returns
    the user with real bearer token headers."""
    from uuid import UUID, uuid4

    from backend.database.db_models import ProjectUserDB, RoleDB, UserDB
    from backend.dependencies.auth_dependencies import get_db
    from backend.main import app
    from backend.services.auth_service import create_access_token

    app.dependency_overrides[get_db] = lambda: rbac_db

    def make(role: str, projects=(), project_role: str | None = None) -> SignedInUser:
        user = UserDB(
            id=uuid4(), email=f"{uuid4().hex}@example.com", full_name=role,
            role=role, password_hash="unused", is_active=True,
        )
        rbac_db.add(user)
        assigned_role = rbac_db.query(RoleDB).filter(RoleDB.name == (project_role or role)).one()
        for project_id in projects:
            rbac_db.add(ProjectUserDB(project_id=UUID(str(project_id)), user_id=user.id, role_id=assigned_role.id))
        rbac_db.commit()
        token = create_access_token(data={"sub": user.email, "role": user.role})
        return SignedInUser(user, token)

    yield make
    app.dependency_overrides.pop(get_db, None)
