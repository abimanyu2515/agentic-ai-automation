import pytest
from uuid import uuid4
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.jira_service import JiraService
from backend.agents.jira_sync_agent import JiraSyncAgent
from backend.models.state import WorkflowState
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase, TestCaseStatus
from backend.models.execution_result import ExecutionResult
from backend.repository.project_repository import get_project_repository


def test_jira_service_mock_mode():
    service = JiraService()
    assert service.mock_mode is True

    # Test create issue
    issue = service.create_issue("Test Story", "Description details", "Story")
    assert issue is not None
    assert "key" in issue
    assert "url" in issue
    assert "browse" in issue["url"]

    # Test search issues
    results = service.search_issues("project = 'QA'")
    assert isinstance(results, list)

    # Test comment
    comment_success = service.add_comment(issue["key"], "Test comment")
    assert comment_success is True

    # Test transition
    transition_success = service.transition_issue(issue["key"], "Done")
    assert transition_success is True

    # Test upload attachment
    attachment_success = service.upload_attachment(issue["key"], "test.png", b"fakebytes", "image/png")
    assert attachment_success is True


def test_jira_sync_agent_sync_user_story(mock_llm_service):
    repo = get_project_repository()
    
    # Create project and requirement context
    project = repo.create_project("Jira Project Test", "Description", "general", "playwright")
    project.jira_project_key = "MOCKPROJ"
    
    req = repo.create_requirement(
        project_id=project.id,
        title="Jira Requirement",
        description="Verify JIRA sync flow",
        priority="high",
        business_domain="test"
    )
    
    # Reload project to have the requirement link
    project.requirements.append(req.id)
    repo.update_project(project)

    # Create approved scenario
    scenario = Scenario(
        requirement_id=req.id,
        scenario_name="Approved Login Flow",
        description="Verify User Story sync is triggered only for approved scenarios",
        approved=True
    )
    repo.save_scenarios([scenario])

    agent = JiraSyncAgent()
    state = WorkflowState(requirement=req, generated_scenarios=[scenario])
    
    # Run the agent for user story sync operation
    config = {
        "configurable": {
            "operation": "sync_user_story",
            "user_id": str(uuid4())
        }
    }
    final_state = agent.run(state, config=config)

    # Verify scenario was updated with synced attributes
    updated_sc = repo.get_scenarios(req.id)[0]
    assert updated_sc.jira_issue_key is not None
    assert updated_sc.jira_issue_url is not None
    assert updated_sc.jira_sync_status == "synced"
    assert updated_sc.jira_last_synced_at is not None


def test_jira_sync_agent_sync_bug(mock_llm_service):
    repo = get_project_repository()
    
    # Create project context
    project = repo.create_project("Bug Project Test", "Description", "general", "playwright")
    
    req = repo.create_requirement(
        project_id=project.id,
        title="Bug Requirement",
        description="Verify bug ticketing flow",
        priority="medium",
        business_domain="test"
    )
    project.requirements.append(req.id)
    repo.update_project(project)

    # Create scenario and test case
    scenario = Scenario(
        requirement_id=req.id,
        scenario_name="Login Failure",
        description="Fails when password is bad",
        approved=True
    )
    repo.save_scenarios([scenario])
    
    tc = TestCase(
        scenario_id=scenario.id,
        title="Verify Login Fails",
        preconditions=[],
        steps=["Step 1"],
        expected_result="Should show password mismatch error"
    )
    repo.save_test_cases([tc])

    # Simulate execution failure
    result = ExecutionResult(
        test_case_id=tc.id,
        status=TestCaseStatus.FAILED,
        error_message="Expected mismatch error but page timed out.",
        duration_seconds=5.2
    )

    agent = JiraSyncAgent()
    state = WorkflowState(requirement=req, execution_results=[result])
    
    # Run the agent for bug sync operation
    config = {
        "configurable": {
            "operation": "sync_bug",
            "user_id": str(uuid4())
        }
    }
    final_state = agent.run(state, config=config)

    # Verify test case status is set to retest_pending and linked to Bug ticket
    updated_tc = repo.get_test_case(tc.id)
    assert updated_tc.status == TestCaseStatus.RETEST_PENDING
    assert updated_tc.jira_issue_key is not None
    assert updated_tc.jira_issue_url is not None
    assert updated_tc.jira_sync_status == "created"


def test_jira_webhook_resolves_status():
    client = TestClient(app)
    repo = get_project_repository()
    
    project = repo.create_project("Webhook Project Test", "Description", "general", "playwright")
    req = repo.create_requirement(
        project_id=project.id,
        title="Webhook Requirement",
        description="Verify webhook",
        priority="medium",
        business_domain="test"
    )
    project.requirements.append(req.id)
    repo.update_project(project)
    
    sc_id = uuid4()
    scenario = Scenario(
        id=sc_id,
        requirement_id=req.id,
        scenario_name="Webhook Mock Scenario",
        description="Fails initially",
        approved=True
    )
    repo.save_scenarios([scenario])
    
    # Save a test case with matching JIRA issue key
    jira_key = "BUG-999"
    tc = TestCase(
        scenario_id=sc_id,
        title="Verify Webhook Triggers Retest",
        preconditions=[],
        steps=["Step 1"],
        expected_result="Test state transitions",
        status=TestCaseStatus.FAILED
    )
    tc.jira_issue_key = jira_key
    repo.save_test_cases([tc])

    # Send webhook payload indicating the JIRA issue was marked "Resolved"
    payload = {
        "issue": {
            "key": jira_key,
            "fields": {
                "status": {
                    "name": "Resolved"
                }
            }
        }
    }
    
    response = client.post("/api/v1/webhooks/jira", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    if getattr(repo, "session", None) is not None:
        updated_tc = repo.get_test_case(tc.id)
        assert updated_tc.status == TestCaseStatus.RETEST_PENDING


def test_import_jira_story():
    from backend.database.db_seeder import seed_database
    from backend.database.db import SessionLocal
    db = SessionLocal()
    try:
        seed_database(db)
    except Exception:
        pass
    finally:
        db.close()

    # Log in with seeded superuser dev@agenticai.com
    client = TestClient(app)
    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "dev@agenticai.com", "password": "devpassword"}
    )
    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    repo = get_project_repository()
    project = repo.create_project("Jira Import Project", "Description", "general", "playwright")

    response = client.post(
        f"/api/v1/projects/{project.id}/requirements/import-jira?issue_key=QA-123",
        headers=headers
    )
    assert response.status_code == 200
    req_data = response.json()
    assert req_data["requirement_id"] == "QA-123"
    assert req_data["jira_issue_key"] == "QA-123"
    assert req_data["jira_sync_status"] == "synced"


def test_jira_sync_service_sync_scenario_created():
    import asyncio
    from backend.services.jira_sync_service import sync_scenario_created
    repo = get_project_repository()
    
    project = repo.create_project("Sync Scenario Test", "Description", "general", "playwright")
    req = repo.create_requirement(
        project_id=project.id,
        title="Sync Req",
        description="Verify sync",
        priority="high",
        business_domain="test"
    )
    project.requirements.append(req.id)
    repo.update_project(project)
    
    scenario = Scenario(
        requirement_id=req.id,
        scenario_name="Scenario for Sync",
        description="Should be sync'd to Jira",
        confidence=0.85,
        approved=True
    )
    repo.save_scenarios([scenario])
    
    # Run sync
    asyncio.run(sync_scenario_created(scenario.id))
    
    updated_sc = repo.get_scenario(scenario.id)
    assert updated_sc.jira_issue_key is not None
    assert updated_sc.jira_issue_id is not None
    assert updated_sc.jira_issue_url is not None
    assert updated_sc.last_jira_sync_status == "SUCCESS"
    assert updated_sc.jira_sync_retry_count == 0


def test_jira_sync_service_sync_scenario_updated():
    import asyncio
    from backend.services.jira_sync_service import sync_scenario_updated
    repo = get_project_repository()
    
    project = repo.create_project("Update Scenario Test", "Description", "general", "playwright")
    req = repo.create_requirement(project_id=project.id, title="Req", description="desc", priority="medium", business_domain="test")
    project.requirements.append(req.id)
    repo.update_project(project)
    
    scenario = Scenario(
        requirement_id=req.id,
        scenario_name="Scenario to Update",
        description="Initial description",
        confidence=0.90,
        approved=True,
        jira_issue_key="QA-100",
        jira_issue_id="10100",
        jira_issue_url="https://jira.com/browse/QA-100"
    )
    repo.save_scenarios([scenario])
    
    # Run update sync
    asyncio.run(sync_scenario_updated(scenario.id))
    
    updated_sc = repo.get_scenario(scenario.id)
    assert updated_sc.last_jira_sync_status == "SUCCESS"
    assert updated_sc.last_jira_sync_error is None


def test_jira_sync_service_sync_execution_finished():
    import asyncio
    from backend.services.jira_sync_service import sync_execution_finished
    repo = get_project_repository()
    
    project = repo.create_project("Exec Finished Test", "Description", "general", "playwright")
    req = repo.create_requirement(project_id=project.id, title="Req", description="desc", priority="medium", business_domain="test")
    project.requirements.append(req.id)
    repo.update_project(project)
    
    scenario = Scenario(
        requirement_id=req.id,
        scenario_name="Scenario for Execution",
        description="desc",
        approved=True,
        jira_issue_key="QA-200",
        jira_issue_id="20200",
        jira_issue_url="https://jira.com/browse/QA-200"
    )
    repo.save_scenarios([scenario])
    
    tc = TestCase(
        scenario_id=scenario.id,
        title="Verify Sync Finished",
        preconditions=[],
        steps=["Step 1"],
        expected_result="PASS"
    )
    repo.save_test_cases([tc])
    
    res = ExecutionResult(
        test_case_id=tc.id,
        status="passed",
        duration_seconds=3.5,
        error_message=None
    )
    repo.save_execution_result(project.id, res)
    
    # Compile a mock report with pdf_path to check resilient upload
    report_payload = {
        "junit_path": "junit.xml",
        "html_path": "report.html",
        "pdf_path": "report.pdf"
    }
    repo.save_report(project.id, res.id, report_payload)
    
    # Call execution finished sync
    asyncio.run(sync_execution_finished(scenario.id, res.id))
    
    updated_sc = repo.get_scenario(scenario.id)
    assert updated_sc.last_jira_sync_status == "SUCCESS"
    
    # Test failed execution path (creates linked bug)
    res_fail = ExecutionResult(
        test_case_id=tc.id,
        status="failed",
        duration_seconds=4.0,
        error_message="Test failed due to timeout."
    )
    repo.save_execution_result(project.id, res_fail)
    
    asyncio.run(sync_execution_finished(scenario.id, res_fail.id))
    
    # Verify bug key is linked on execution result
    updated_res = repo.get_execution_result(project.id, res_fail.id)
    assert updated_res.jira_bug_id is not None
    assert updated_res.jira_bug_url is not None
