import os
from pathlib import Path
import pytest
from uuid import uuid4

from backend.models.requirement import Requirement, RequirementSource
from backend.models.scenario import Scenario
from backend.services.workflow_service import WorkflowService
from backend.repository.project_repository import JSONProjectRepository
import backend.repository.project_repository as repo_module
from backend.agents.requirement_analyst_agent import RequirementAnalystAgent
from backend.agents.feature_inventory_agent import FeatureInventoryAgent
from backend.agents.backlog_creation_agent import BacklogCreationAgent
from backend.graph.workflow import build_graph
from backend.models.state import WorkflowState
from backend.models.operation import WorkflowOperation

TEST_STORE_PATH = Path(__file__).parent / "test_ba_project_store.json"

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

def test_requirement_analyst_agent_deterministic(stub_llm_service):
    """Verifies that the RequirementAnalystAgent runs deterministically and persists values."""
    agent = RequirementAnalystAgent(llm_service=stub_llm_service)
    
    # Text triggers deterministic domain "Auth" and priority "high"
    req = Requirement(
        title="Valid Auth Flow",
        description="We must check the auth credentials for critical logins.",
        source=RequirementSource.MANUAL
    )
    
    # We create the project and register the requirement first to mimic flow
    repo = repo_module.get_project_repository()
    project = repo.create_project("Test Proj", "Desc")
    req = repo.create_requirement(
        project_id=project.id,
        title=req.title,
        description=req.description,
        priority="medium",
        business_domain="general"
    )
    
    state = WorkflowState(requirement=req)
    final_state = agent.run(state)
    
    # It shouldn't trigger LLM because name is not manual placeholder and domain was guessable.
    assert "Using deterministic parser metadata" in "".join(final_state.logs)
    
    # Fetch from repo to check persistence
    req_meta = repo.get_requirement(req.id)
    assert req_meta.priority == "high"
    assert req_meta.business_domain == "Auth"

def test_requirement_analyst_agent_llm(stub_llm_service):
    """Verifies that the RequirementAnalystAgent invokes LLM enrichment for default manual requirements."""
    agent = RequirementAnalystAgent(llm_service=stub_llm_service)
    
    req = Requirement(
        title="REQ-MANUAL-001",
        description="General description text.",
        source=RequirementSource.MANUAL
    )
    
    repo = repo_module.get_project_repository()
    project = repo.create_project("Test Proj", "Desc")
    req = repo.create_requirement(
        project_id=project.id,
        title=req.title,
        description=req.description,
        priority="medium",
        business_domain="general"
    )
    
    state = WorkflowState(requirement=req)
    final_state = agent.run(state)
    
    assert "Invoking LLM for metadata enrichment" in "".join(final_state.logs)
    assert final_state.requirement.title == "Enriched Requirement Title"
    
    req_meta = repo.get_requirement(req.id)
    assert req_meta.priority == "high"
    assert req_meta.business_domain == "Finance"

def test_feature_inventory_agent_graceful_bypass(stub_llm_service):
    """Verifies that the FeatureInventoryAgent skips mapping gracefully when RAG is disabled."""
    agent = FeatureInventoryAgent()
    req = Requirement(
        title="Test Req",
        description="Some description.",
        source=RequirementSource.MANUAL
    )
    state = WorkflowState(requirement=req)
    final_state = agent.run(state)
    
    # Should skip mapping gracefully
    assert "Skipping feature inventory mapping" in "".join(final_state.logs)

def test_backlog_creation_agent_success(stub_llm_service):
    """Verifies that the BacklogCreationAgent writes backlog items to scenario notes."""
    agent = BacklogCreationAgent(llm_service=stub_llm_service)
    
    req = Requirement(
        title="Test Req",
        description="Requirement description.",
        source=RequirementSource.MANUAL
    )
    
    scenario = Scenario(
        requirement_id=req.id,
        scenario_name="Login attempt",
        description="User tries to log in",
        approved=True
    )
    
    repo = repo_module.get_project_repository()
    # Save scenario first
    repo.save_scenarios([scenario])
    
    state = WorkflowState(requirement=req, generated_scenarios=[scenario])
    final_state = agent.run(state)
    
    assert "Generated user story for scenario" in "".join(final_state.logs)
    
    # Check scenario notes in repository
    notes = repo.get_scenario_notes(scenario.id)
    assert len(notes) == 1
    assert "Agile User Story" in notes[0]
    assert "acceptance criteria" in notes[0].lower()

def test_workflow_service_e2e_ingestion(mock_llm_service):
    """Test full ingestion workflow integration via WorkflowService."""
    ws = WorkflowService()
    project = ws.repo.create_project("BA Project", "E2E Ingestion test")
    
    # Draft requirement
    req = ws.repo.create_requirement(
        project_id=project.id,
        title="REQ-MANUAL-1",
        description="Critical Auth flow verification.",
        priority="medium",
        business_domain="general"
    )
    
    finalized = ws.ingest_requirement(project.id, req)
    
    assert finalized.title == "Enriched Requirement Title"
    
    # Verify database entry has priority/domain updated
    req_meta = ws.repo.get_requirement(req.id)
    assert req_meta.priority == "high"
    assert req_meta.business_domain == "Finance"

def test_workflow_service_independent_backlog(mock_llm_service):
    """Test independent backlog generation route in WorkflowService."""
    ws = WorkflowService()
    project = ws.repo.create_project("BA Project", "E2E Backlog test")
    req = ws.repo.create_requirement(project.id, "Auth Flow", "Critical auth flow verification", "high", "Auth")
    
    sc = Scenario(requirement_id=req.id, scenario_name="Valid Auth", description="User authenticates", approved=True)
    ws.repo.save_scenarios([sc])
    
    # Call generate_backlog independently
    scenarios = ws.generate_backlog(project.id, req.id)
    assert len(scenarios) == 1
    
    notes = ws.repo.get_scenario_notes(sc.id)
    assert len(notes) == 1
    assert "Agile User Story" in notes[0]
