import pytest
from uuid import uuid4
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.execution_result import ExecutionStatus
from backend.models.test_case import TestCaseStatus
from backend.services.project_service import ProjectService

client = TestClient(app)

def test_playwright_webhook_flow():
    # 1. Setup a dummy project and test case
    project_service = ProjectService()
    project = project_service.create_project("Test Project", "Description", "insurance")
    
    req = project_service.create_requirement(
        project.id, "Req Title", "Req Description", "HIGH", "claims"
    )
    
    # Create scenario
    scenario_id = uuid4()
    from backend.models.scenario import Scenario
    from backend.models.common import Priority
    scenario = Scenario(
        id=scenario_id,
        requirement_id=req.id,
        scenario_name="Test Scenario",
        description="Test Desc",
        priority=Priority.HIGH,
        confidence=0.9,
        approved=True
    )
    project_service.repo.save_scenarios([scenario])
    
    # Create testcase
    test_case_id = uuid4()
    from backend.models.test_case import TestCase, TestCaseStatus, EvaluationStatus
    test_case = TestCase(
        id=test_case_id,
        scenario_id=scenario_id,
        title="Login verification",
        preconditions=[],
        steps=["Open page", "Verify text"],
        expected_result="Logged in",
        priority=Priority.HIGH,
        status=TestCaseStatus.PENDING,
        confidence=0.95,
        evaluation_status=EvaluationStatus.APPROVED,
        playwright_script="await page.goto('/')"
    )
    project_service.repo.save_test_cases([test_case])
    
    # 2. Trigger webhook - STARTED
    execution_id = uuid4()
    started_payload = {
        "execution_id": str(execution_id),
        "test_case_id": str(test_case_id),
        "event": "started",
        "status": "running"
      }
    response = client.post(f"/api/v1/projects/{project.id}/executions/webhook", json=started_payload)
    assert response.status_code == 200
    
    # Check active stream events
    from backend.services.workflow_service import active_execution_events
    events = active_execution_events.get(str(test_case_id))
    assert events is not None
    assert len(events) == 1
    assert events[0]["status"] == "Running"
    
    # 3. Trigger webhook - UPDATED (Timeline / Log)
    updated_payload = {
        "execution_id": str(execution_id),
        "test_case_id": str(test_case_id),
        "event": "updated",
        "status": "running",
        "log_line": "Page loaded successfully",
        "timeline_event": {
            "event": "Page loaded",
            "type": "success"
        }
    }
    response = client.post(f"/api/v1/projects/{project.id}/executions/webhook", json=updated_payload)
    assert response.status_code == 200
    assert len(events) == 2
    assert events[1]["log"] == "Page loaded successfully"
    assert events[1]["timeline"] == "Page loaded"
    
    # 4. Trigger webhook - COMPLETED (Passed)
    completed_payload = {
        "execution_id": str(execution_id),
        "test_case_id": str(test_case_id),
        "event": "completed",
        "status": "passed",
        "duration_seconds": 4.5,
        "screenshot_path": "screenshots/final.png",
        "video_path": "video/run.webm",
        "trace_path": "trace/trace.zip",
        "error_message": None
    }
    response = client.post(f"/api/v1/projects/{project.id}/executions/webhook", json=completed_payload)
    assert response.status_code == 200
    
    # Verify execution result in database
    executions = project_service.get_execution_results(project.id)
    assert len(executions) == 1
    result = executions[0]
    assert result.status == ExecutionStatus.PASSED
    assert result.duration_seconds == 4.5
    assert result.screenshot_path == "screenshots/final.png"
    assert result.video_path == "video/run.webm"
    assert result.trace_path == "trace/trace.zip"
    
    # Verify testcase execution status is updated to passed (approval status is IMMUTABLE)
    tc = project_service.get_test_case(test_case_id)
    assert tc is not None
    assert tc.status == TestCaseStatus.PASSED
    assert tc.evaluation_status == "approved"  # Immutable!
    
    # Clean up project
    project_service.delete_project(project.id)
