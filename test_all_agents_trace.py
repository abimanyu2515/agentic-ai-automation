"""
Comprehensive Agent Trace Test

This script creates a complete trace showing all agents in the workflow hierarchy.
After running, check LangSmith dashboard to see all agents traced.

URL: https://smith.langchain.com → "My Project"
"""

import os
import sys
from uuid import uuid4

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Configure LangSmith from settings
from backend.config.settings import get_settings

settings = get_settings()

print("=" * 80)
print("COMPLETE AGENT WORKFLOW TRACE TEST")
print("=" * 80)
print(f"LangSmith Project: {settings.langsmith.project_name}")
print(f"Tracing Enabled: {settings.langsmith.tracing_enabled}")
print("=" * 80)

if not settings.langsmith.tracing_enabled:
    print("\n❌ ERROR: LangSmith tracing is disabled")
    sys.exit(1)

# Set environment variables for LangSmith SDK
os.environ['LANGSMITH_TRACING'] = 'true'
os.environ['LANGCHAIN_TRACING_V2'] = 'true'
os.environ['LANGSMITH_API_KEY'] = settings.langsmith.api_key
os.environ['LANGCHAIN_API_KEY'] = settings.langsmith.api_key
os.environ['LANGSMITH_ENDPOINT'] = settings.langsmith.endpoint
os.environ['LANGCHAIN_ENDPOINT'] = settings.langsmith.endpoint
os.environ['LANGSMITH_PROJECT'] = settings.langsmith.project_name
os.environ['LANGCHAIN_PROJECT'] = settings.langsmith.project_name

print("\n✓ LangSmith environment configured")

# Import after environment is configured
from langsmith import traceable
from backend.models.requirement import Requirement
from backend.models.state import WorkflowState
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase, EvaluationStatus
from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.agents.scenario_agent import ScenarioAgent
from backend.agents.test_case_agent import TestCaseAgent
from backend.agents.evaluation_agent import EvaluationAgent
from backend.agents.playwright_agent import PlaywrightAgent
from backend.agents.execution_agent import ExecutionAgent
from backend.agents.report_agent import ReportAgent
from datetime import datetime, timezone

print("\n✓ All modules imported")

# Create a root trace for the entire workflow
@traceable(name="Complete_Workflow_Test", project_name=settings.langsmith.project_name)
def run_complete_workflow_test():
    """
    Simulate a complete workflow with all agents.
    Each agent will create a child trace.
    """
    
    print("\n" + "=" * 80)
    print("STARTING WORKFLOW SIMULATION")
    print("=" * 80)
    
    # Step 1: Create test requirement
    print("\n[Step 1] Creating test requirement...")
    requirement = Requirement(
        id=uuid4(),
        title="Test Complete Agent Tracing",
        description="This is a comprehensive test to verify all agents are traced in LangSmith in a unified hierarchy"
    )
    print(f"  ✓ Requirement ID: {requirement.id}")
    
    # Step 2: Initialize workflow state
    print("\n[Step 2] Initializing workflow state...")
    state = WorkflowState(requirement=requirement)
    state.add_log("Workflow test started")
    print("  ✓ Workflow state created")
    
    # Step 3: Mock ScenarioAgent
    print("\n[Step 3] Simulating ScenarioAgent...")
    
    @traceable(name="ScenarioAgent", project_name=settings.langsmith.project_name)
    def mock_scenario_agent(state):
        print("  → ScenarioAgent processing...")
        scenario = Scenario(
            id=uuid4(),
            requirement_id=requirement.id,
            scenario_name="Complete Booking Flow",
            description="Test scenario covering complete hotel booking workflow",
            test_data={"location": "Sydney", "hotel": "Hotel Creek"},
            approved=True,
            reviewer="AutoTest"
        )
        state.generated_scenarios = [scenario]
        state.selected_scenario_ids.append(scenario.id)
        state.add_log("ScenarioAgent: Generated 1 scenario")
        print("  ✓ ScenarioAgent completed")
        return state
    
    state = mock_scenario_agent(state)
    
    # Step 4: Mock TestCaseAgent
    print("\n[Step 4] Simulating TestCaseAgent...")
    
    @traceable(name="TestCaseAgent", project_name=settings.langsmith.project_name)
    def mock_testcase_agent(state):
        print("  → TestCaseAgent processing...")
        test_case = TestCase(
            id=uuid4(),
            scenario_id=state.generated_scenarios[0].id,
            title="Verify successful hotel booking",
            preconditions=["User has valid credentials"],
            steps=[
                "Login to application",
                "Search for hotel",
                "Select hotel",
                "Complete booking"
            ],
            expected_result="Booking confirmation displayed",
            test_case_ref_id="US01-TC01"
        )
        state.generated_test_cases = [test_case]
        state.add_log("TestCaseAgent: Generated 1 test case")
        print("  ✓ TestCaseAgent completed")
        return state
    
    state = mock_testcase_agent(state)
    
    # Step 5: Mock EvaluationAgent
    print("\n[Step 5] Simulating EvaluationAgent...")
    
    @traceable(name="EvaluationAgent", project_name=settings.langsmith.project_name)
    def mock_evaluation_agent(state):
        print("  → EvaluationAgent processing...")
        state.generated_test_cases[0].evaluation_status = EvaluationStatus.APPROVED
        state.generated_test_cases[0].evaluation_reason = "Test case is comprehensive and follows best practices"
        state.add_log("EvaluationAgent: Approved 1 test case")
        print("  ✓ EvaluationAgent completed")
        return state
    
    state = mock_evaluation_agent(state)
    
    # Step 6: Mock PlaywrightAgent
    print("\n[Step 6] Simulating PlaywrightAgent...")
    
    @traceable(name="PlaywrightAgent", project_name=settings.langsmith.project_name)
    def mock_playwright_agent(state):
        print("  → PlaywrightAgent processing...")
        state.generated_test_cases[0].playwright_script = """
        import { test, expect } from '@playwright/test';
        
        test('Verify successful hotel booking', async ({ page }) => {
            // Test implementation
            await page.goto('https://adactinhotelapp.com');
            await expect(page).toHaveTitle(/Adactin/);
        });
        """
        state.add_log("PlaywrightAgent: Generated script for 1 test case")
        print("  ✓ PlaywrightAgent completed")
        return state
    
    state = mock_playwright_agent(state)
    
    # Step 7: Mock ExecutionAgent
    print("\n[Step 7] Simulating ExecutionAgent...")
    
    @traceable(name="ExecutionAgent", project_name=settings.langsmith.project_name)
    def mock_execution_agent(state):
        print("  → ExecutionAgent processing...")
        execution_result = ExecutionResult(
            id=uuid4(),
            test_case_id=state.generated_test_cases[0].id,
            status=ExecutionStatus.PASSED,
            duration_seconds=12.5,
            executed_at=datetime.now(timezone.utc)
        )
        state.execution_results = [execution_result]
        state.add_log("ExecutionAgent: Executed 1 test case - PASSED")
        print("  ✓ ExecutionAgent completed")
        return state
    
    state = mock_execution_agent(state)
    
    # Step 8: Mock ReportAgent
    print("\n[Step 8] Simulating ReportAgent...")
    
    @traceable(name="ReportAgent", project_name=settings.langsmith.project_name)
    def mock_report_agent(state):
        print("  → ReportAgent processing...")
        state.add_log("ReportAgent: Generated execution report")
        print("  ✓ ReportAgent completed")
        return state
    
    state = mock_report_agent(state)
    
    # Step 9: Mock additional agents for complete coverage
    print("\n[Step 9] Simulating additional agents...")
    
    @traceable(name="HumanApprovalAgent", project_name=settings.langsmith.project_name)
    def mock_human_approval_agent(state):
        print("  → HumanApprovalAgent processing...")
        state.human_approved_test_case_ids.append(state.generated_test_cases[0].id)
        state.add_log("HumanApprovalAgent: Approved test cases")
        print("  ✓ HumanApprovalAgent completed")
        return state
    
    state = mock_human_approval_agent(state)
    
    @traceable(name="SupervisorAgent", project_name=settings.langsmith.project_name)
    def mock_supervisor_agent(state):
        print("  → SupervisorAgent processing...")
        state.add_log("SupervisorAgent: Routing workflow")
        print("  ✓ SupervisorAgent completed")
        return state
    
    state = mock_supervisor_agent(state)
    
    # Final summary
    print("\n" + "=" * 80)
    print("WORKFLOW SIMULATION COMPLETE")
    print("=" * 80)
    print(f"\nWorkflow Summary:")
    print(f"  • Scenarios Generated: {len(state.generated_scenarios)}")
    print(f"  • Test Cases Generated: {len(state.generated_test_cases)}")
    print(f"  • Test Cases Approved: {len(state.human_approved_test_case_ids)}")
    print(f"  • Executions Run: {len(state.execution_results)}")
    print(f"  • Execution Status: {state.execution_results[0].status.value.upper()}")
    print(f"\nTotal Agents Traced: 8")
    print(f"  1. SupervisorAgent")
    print(f"  2. ScenarioAgent")
    print(f"  3. TestCaseAgent")
    print(f"  4. EvaluationAgent")
    print(f"  5. HumanApprovalAgent")
    print(f"  6. PlaywrightAgent")
    print(f"  7. ExecutionAgent")
    print(f"  8. ReportAgent")
    
    return state

# Execute the complete workflow test
try:
    print("\n🚀 Executing complete workflow with all agents...\n")
    final_state = run_complete_workflow_test()
    
    print("\n" + "=" * 80)
    print("✅ SUCCESS! All agents traced successfully!")
    print("=" * 80)
    print(f"\nView your unified trace in LangSmith:")
    print(f"  1. Open: https://smith.langchain.com")
    print(f"  2. Select project: '{settings.langsmith.project_name}'")
    print(f"  3. Look for trace: 'Complete_Workflow_Test'")
    print(f"\nYou should see a hierarchical trace with all 8 agents:")
    print(f"  Complete_Workflow_Test (root)")
    print(f"  ├── ScenarioAgent")
    print(f"  ├── TestCaseAgent")
    print(f"  ├── EvaluationAgent")
    print(f"  ├── PlaywrightAgent")
    print(f"  ├── ExecutionAgent")
    print(f"  ├── ReportAgent")
    print(f"  ├── HumanApprovalAgent")
    print(f"  └── SupervisorAgent")
    print("\n" + "=" * 80)
    
except Exception as e:
    print(f"\n❌ ERROR during workflow execution: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
