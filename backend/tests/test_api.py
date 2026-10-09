import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.repository.project_repository import JSONProjectRepository, get_project_repository
import backend.repository.project_repository as repo_module

TEST_STORE_PATH = Path(__file__).parent / "test_project_store.json"


@pytest.fixture(autouse=True)
def setup_test_db():
    # Clean up test JSON DB if it exists
    if TEST_STORE_PATH.exists():
        os.remove(TEST_STORE_PATH)
    
    # Point active project repository to our test DB file
    orig_repo = repo_module._active_project_repo
    test_repo = JSONProjectRepository(file_path=TEST_STORE_PATH)
    repo_module._active_project_repo = test_repo
    
    yield
    
    # Restore original repository and clean up test JSON DB after test
    repo_module._active_project_repo = orig_repo
    if TEST_STORE_PATH.exists():
        os.remove(TEST_STORE_PATH)


@pytest.fixture
def client():
    return TestClient(app)


def test_create_and_list_projects(client):
    # Check initially empty
    response = client.get("/api/v1/projects")
    assert response.status_code == 200
    assert response.json() == []

    # Create a project
    response = client.post("/api/v1/projects", json={"name": "Test Project", "description": "Desc"})
    assert response.status_code == 201
    proj = response.json()
    assert proj["name"] == "Test Project"
    assert proj["description"] == "Desc"
    assert "id" in proj

    # Check listed
    response = client.get("/api/v1/projects")
    assert response.status_code == 200
    projs = response.json()
    assert len(projs) == 1
    assert projs[0]["id"] == proj["id"]


def test_create_and_get_requirements(client):
    # Create project
    response = client.post("/api/v1/projects", json={"name": "Test Proj", "description": "A test requirement project"})
    proj_id = response.json()["id"]

    # Create requirement
    response = client.post(
        f"/api/v1/projects/{proj_id}/requirements",
        json={
            "title": "Req Title",
            "description": "Req Desc",
            "priority": "high",
            "business_domain": "finance"
        }
    )
    assert response.status_code == 201
    req = response.json()
    assert req["title"] == "Req Title"
    assert req["description"] == "Req Desc"

    # Get requirements
    response = client.get(f"/api/v1/projects/{proj_id}/requirements")
    assert response.status_code == 200
    reqs = response.json()
    assert len(reqs) == 1
    assert reqs[0]["title"] == "Req Title"
    assert reqs[0]["priority"] == "high"


def test_get_and_write_settings(client, monkeypatch):
    # Temporarily remove env overrides so PUT settings can be verified
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    from backend.config.settings import get_settings
    get_settings.cache_clear()

    # Get settings
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    cfg = response.json()
    assert "llm" in cfg
    assert "workflow" in cfg

    # Write settings
    response = client.put(
        "/api/v1/settings",
        json={
            "llm": {
                "provider": "ollama",
                "model": "llama3.1",
                "temperature": 0.5
            }
        }
    )
    assert response.status_code == 200
    updated_cfg = response.json()["settings"]
    assert updated_cfg["llm"]["provider"] == "ollama"
    assert updated_cfg["llm"]["temperature"] == 0.5

    # Cleanup settings override.yaml if created
    override_path = Path(__file__).parent.parent / "config" / "override.yaml"
    if override_path.exists():
        os.remove(override_path)
    get_settings.cache_clear()


def test_update_and_delete_project(client):
    # Create project
    response = client.post("/api/v1/projects", json={"name": "Temp Project", "description": "Temporary project description"})
    proj_id = response.json()["id"]

    # Update project
    response = client.put(f"/api/v1/projects/{proj_id}", json={"name": "Updated Title", "description": "Updated Desc"})
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Updated Title"
    assert updated["description"] == "Updated Desc"

    # Delete project
    response = client.delete(f"/api/v1/projects/{proj_id}")
    assert response.status_code == 200
    assert response.json() == {"detail": "Project deleted successfully"}

    # Getting deleted project fails
    response = client.get(f"/api/v1/projects/{proj_id}")
    assert response.status_code == 404


def test_create_and_delete_documents(client):
    # Create project
    response = client.post("/api/v1/projects", json={"name": "Doc Project", "description": "Doc project description"})
    proj_id = response.json()["id"]

    # Create document metadata
    response = client.post(
        f"/api/v1/projects/{proj_id}/documents",
        json={
            "filename": "requirements.pdf",
            "original_filename": "Healthcare_v1.pdf",
            "mime_type": "application/pdf",
            "size": 150000,
            "storage_path": "/data/requirements.pdf"
        }
    )
    assert response.status_code == 201
    doc = response.json()
    assert doc["filename"] == "requirements.pdf"
    assert doc["embedding_status"] == "pending"

    # List documents
    response = client.get(f"/api/v1/projects/{proj_id}/documents")
    assert response.status_code == 200
    docs = response.json()
    assert len(docs) == 1
    assert docs[0]["filename"] == "requirements.pdf"

    # Delete document
    response = client.delete(f"/api/v1/projects/{proj_id}/documents/{doc['id']}")
    assert response.status_code == 200

    # Listed documents are empty now
    response = client.get(f"/api/v1/projects/{proj_id}/documents")
    assert response.json() == []


def test_get_activity_traces(client):
    # Create project
    response = client.post("/api/v1/projects", json={"name": "Trace Proj", "description": "Trace project description"})
    proj_id = response.json()["id"]

    # Get traces
    response = client.get(f"/api/v1/projects/{proj_id}/traces")
    assert response.status_code == 200
    trace_data = response.json()
    assert "total_tokens" in trace_data
    assert "traces" in trace_data


def test_project_with_line_of_business(client):
    # Create project with LOB
    response = client.post(
        "/api/v1/projects", 
        json={"name": "Finance Platform", "description": "LOB project", "line_of_business": "Banking"}
    )
    assert response.status_code == 201
    proj = response.json()
    assert proj["line_of_business"] == "Banking"

    # List projects checks LOB
    response = client.get("/api/v1/projects")
    assert response.status_code == 200
    projs = response.json()
    assert any(p["line_of_business"] == "Banking" for p in projs)


def test_notes_creation(client):
    from uuid import uuid4
    sc_id = uuid4()
    tc_id = uuid4()

    # Create scenario notes
    response = client.post(f"/api/v1/scenarios/{sc_id}/notes", json={"note": "Scenario remark content"})
    assert response.status_code == 200

    # Get scenario notes
    response = client.get(f"/api/v1/scenarios/{sc_id}/notes")
    assert response.status_code == 200
    notes = response.json()
    assert len(notes) == 1
    assert "Scenario remark content" in notes[0]

    # Create testcase notes
    response = client.post(f"/api/v1/testcases/{tc_id}/notes", json={"note": "Test case remark content"})
    assert response.status_code == 200

    # Get testcase notes
    response = client.get(f"/api/v1/testcases/{tc_id}/notes")
    assert response.status_code == 200
    tc_notes = response.json()
    assert len(tc_notes) == 1
    assert "Test case remark content" in tc_notes[0]


def test_scenario_replacement_and_rejection_cascades(client):
    # 1. Setup project & requirement
    response = client.post("/api/v1/projects", json={"name": "Cascade Proj", "description": "Cascade project description"})
    proj_id = response.json()["id"]

    response = client.post(
        f"/api/v1/projects/{proj_id}/requirements",
        json={"title": "REQ-1: Auth Security", "description": "Strict passwords", "priority": "high", "business_domain": "general"}
    )
    req_id = response.json()["id"]

    # 2. Add a mock scenario and test case to verify cascaded deletion
    from backend.repository.project_repository import get_project_repository
    from backend.models.scenario import Scenario
    from backend.models.test_case import TestCase
    from uuid import uuid4

    repo = get_project_repository()
    sc_id = uuid4()
    scenario = Scenario(id=sc_id, requirement_id=req_id, scenario_name="Cascade Scenario", description="desc", approved=True)
    repo.save_scenarios([scenario])

    tc_id = uuid4()
    tc = TestCase(id=tc_id, scenario_id=sc_id, title="Child Test Case", steps=["Step 1"], expected_result="Done", evaluation_status="approved", confidence=1.0)
    repo.save_test_cases([tc])

    # 3. Reject scenario (approved = False) and check test case deletion
    response = client.put(f"/api/v1/projects/{proj_id}/scenarios/{sc_id}", json={"approved": False})
    assert response.status_code == 200
    assert response.json()["approved"] is False

    # Check child test case is deleted automatically from database
    tcs = repo.get_test_cases(sc_id)
    assert len(tcs) == 0

    # 4. Clear/Replace Scenarios test
    repo.save_scenarios([scenario])
    assert len(repo.get_scenarios(req_id)) == 1
    
    # Trigger replace mode clear via service
    from backend.services.workflow_service import WorkflowService
    ws = WorkflowService()
    ws.repo.clear_scenarios_for_requirement(req_id)
    assert len(repo.get_scenarios(req_id)) == 0


def test_create_project_empty_description_validation(client):
    # Test project creation with empty description (whitespaces)
    response = client.post("/api/v1/projects", json={"name": "Empty Desc Proj", "description": "   "})
    assert response.status_code == 400
    assert "Project description cannot be empty" in response.json()["detail"]

    # Test project creation with missing description field
    response = client.post("/api/v1/projects", json={"name": "Missing Desc Proj"})
    assert response.status_code == 422


def test_postman_ingestion(client):
    # 1. Setup project
    response = client.post("/api/v1/projects", json={"name": "Postman Proj", "description": "Postman project description"})
    proj_id = response.json()["id"]

    # 2. Upload dummy Postman collection JSON
    postman_data = {
        "info": {
            "name": "Dummy Collection",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "item": [
            {
                "name": "Auth",
                "item": [
                    {
                        "name": "Login User",
                        "request": {
                            "method": "POST",
                            "header": [
                                {"key": "Content-Type", "value": "application/json"}
                            ],
                            "body": {
                                "mode": "raw",
                                "raw": '{"username": "user", "password": "pwd"}'
                            },
                            "url": {
                                "raw": "https://api.test.com/login"
                            },
                            "description": "Log in to retrieve bearer token."
                        }
                    }
                ]
            }
        ]
    }
    
    import json
    file_content = json.dumps(postman_data).encode("utf-8")
    
    response = client.post(
        f"/api/v1/projects/{proj_id}/requirements/import",
        files={"file": ("collection.json", file_content, "application/json")}
    )
    assert response.status_code == 200
    reqs = response.json()
    assert len(reqs) == 1
    assert reqs[0]["title"] == "API Request: Login User"
    assert "Postman Ingestion Source" in reqs[0]["description"]
    assert "https://api.test.com/login" in reqs[0]["description"]
    assert "Log in to retrieve bearer token." in reqs[0]["description"]


def test_batch_execution_endpoints(client):
    # 1. Setup project
    response = client.post("/api/v1/projects", json={"name": "Batch API Proj", "description": "Batch API project"})
    assert response.status_code == 201
    proj_id = response.json()["id"]

    # 2. Setup requirement and testcase
    from backend.repository.project_repository import get_project_repository
    from backend.models.scenario import Scenario
    from backend.models.test_case import TestCase
    from uuid import uuid4

    repo = get_project_repository()
    sc_id = uuid4()
    scenario = Scenario(id=sc_id, requirement_id=uuid4(), scenario_name="API Scenario", description="desc", approved=True)
    repo.save_scenarios([scenario])

    tc_id = uuid4()
    tc = TestCase(
        id=tc_id, 
        scenario_id=sc_id, 
        title="API Test Case", 
        steps=["Step 1"], 
        expected_result="Done", 
        evaluation_status="approved", 
        confidence=1.0,
        playwright_script="await page.goto('https://google.com');"
    )
    repo.save_test_cases([tc])

    # 3. Trigger batch execution
    response = client.post(
        f"/api/v1/projects/{proj_id}/batches/execute",
        json=[str(tc_id)]
    )
    assert response.status_code == 200
    res = response.json()
    assert res["status"] == "started"
    assert "batch_id" in res
    batch_id = res["batch_id"]

    # 4. Read stream connection event
    response = client.get(f"/api/v1/projects/{proj_id}/batches/{batch_id}/stream")
    assert response.status_code == 200
    assert "Connecting" in response.text



