import os
from pathlib import Path
import pytest
import unittest.mock as mock
from uuid import uuid4

from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase, EvaluationStatus, TestCaseStatus
from backend.services.workflow_service import WorkflowService, run_execution_and_stream
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


def test_generate_playwright_script_via_langgraph(mock_llm_service):
    ws = WorkflowService()
    project = ws.repo.create_project("LangGraph Project", "Testing LangGraph integration")
    req = ws.repo.create_requirement(project.id, "Req Title", "Req Description", "high", "finance")

    sc = Scenario(requirement_id=req.id, scenario_name="Valid Login", description="desc", approved=True)
    ws.repo.save_scenarios([sc])

    tc = TestCase(
        scenario_id=sc.id,
        title="Verify Login",
        steps=["Step 1"],
        expected_result="LoggedIn",
        evaluation_status=EvaluationStatus.APPROVED,
        confidence=0.9
    )
    ws.repo.save_test_cases([tc])

    updated_tc = ws.generate_playwright_script(project.id, tc.id)
    assert updated_tc.playwright_script is not None
    assert "test(" in updated_tc.playwright_script


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
    assert len(results) == 1
    assert results[0].status.value == "passed"
