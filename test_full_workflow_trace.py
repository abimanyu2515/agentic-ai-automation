"""
Test script to trigger a full workflow execution that creates traces for ALL agents.

This will:
1. Create a test requirement
2. Execute the complete workflow through all agents
3. Send traces to LangSmith showing all agents in one unified hierarchy

Run this script, then check https://smith.langchain.com to see all agents traced.
"""

import os
import sys
from uuid import uuid4

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

print("=" * 80)
print("Full Workflow Trace Test - All Agents")
print("=" * 80)

# Verify LangSmith is configured
from backend.config.settings import get_settings
settings = get_settings()

print(f"✓ LangSmith Tracing: {settings.langsmith.tracing_enabled}")
print(f"✓ Project: {settings.langsmith.project_name}")
print(f"✓ API Key: {'SET' if settings.langsmith.api_key else 'NOT SET'}")

if not settings.langsmith.tracing_enabled or not settings.langsmith.api_key:
    print("\n❌ ERROR: LangSmith is not properly configured")
    sys.exit(1)

print("=" * 80)

# Import workflow components
from backend.models.requirement import Requirement
from backend.graph.workflow import graph, run_workflow

# Create a simple test requirement
print("\n📝 Creating test requirement...")
requirement = Requirement(
    id=uuid4(),
    title="Test Hotel Booking - LangSmith Trace Demo",
    description="""
As a user, I want to book a hotel room through the Adactin Hotel App.

Acceptance Criteria:
1. User can search for available hotels
2. User can select a hotel from search results
3. User can book the selected hotel
4. User receives booking confirmation with order number

Test Data:
- Location: Sydney
- Hotel: Hotel Creek
- Room Type: Standard
- Number of Rooms: 1
- Check In Date: Tomorrow
- Check Out Date: Day after tomorrow
- Adults per Room: 2
- Children per Room: 0
"""
)

print(f"✓ Requirement created")
print(f"  ID: {requirement.id}")
print(f"  Title: {requirement.title}")

print("\n" + "=" * 80)
print("🚀 Starting workflow execution with ALL agents...")
print("=" * 80)
print("\nThis will execute the following agents in sequence:")
print("  1. SupervisorAgent - Route workflow")
print("  2. ScenarioAgent - Generate test scenarios (LLM call)")
print("  3. HumanApprovalAgent - Auto-approve scenarios")
print("  4. TestCaseAgent - Generate test cases (LLM call)")
print("  5. EvaluationAgent - Evaluate test cases (LLM call)")
print("  6. HumanApprovalAgent - Auto-approve test cases")
print("  7. PlaywrightAgent - Generate Playwright scripts (LLM call)")
print("  8. ExecutionAgent - Execute tests")
print("  9. ReportAgent - Generate reports")
print("\nAll agents will be traced in LangSmith as one unified hierarchy!")
print("=" * 80)

try:
    # Execute the workflow
    print("\n⏳ Executing workflow... (this may take 1-2 minutes)")
    print("   (Calling real LLMs to generate scenarios, test cases, and scripts)")
    
    final_state = run_workflow(requirement)
    
    print("\n" + "=" * 80)
    print("✅ Workflow execution completed successfully!")
    print("=" * 80)
    
    # Display results
    if final_state.generated_scenarios:
        print(f"\n📊 Results:")
        print(f"  - Scenarios Generated: {len(final_state.generated_scenarios)}")
        for i, scenario in enumerate(final_state.generated_scenarios, 1):
            print(f"    {i}. {scenario.scenario_name}")
    
    if final_state.generated_test_cases:
        print(f"  - Test Cases Generated: {len(final_state.generated_test_cases)}")
        for i, tc in enumerate(final_state.generated_test_cases[:3], 1):
            print(f"    {i}. {tc.title}")
        if len(final_state.generated_test_cases) > 3:
            print(f"    ... and {len(final_state.generated_test_cases) - 3} more")
    
    if final_state.execution_results:
        print(f"  - Tests Executed: {len(final_state.execution_results)}")
        passed = sum(1 for r in final_state.execution_results if r.status.value == "passed")
        failed = sum(1 for r in final_state.execution_results if r.status.value == "failed")
        print(f"    Passed: {passed}, Failed: {failed}")
    
    print("\n" + "=" * 80)
    print("🎉 ALL AGENTS TRACED IN LANGSMITH!")
    print("=" * 80)
    print("\n📍 View the trace in LangSmith:")
    print("   1. Go to: https://smith.langchain.com")
    print(f"   2. Select project: '{settings.langsmith.project_name}'")
    print("   3. Look for the most recent trace")
    print("   4. You should see ALL agents in one unified hierarchy:")
    print("")
    print("      run_workflow")
    print("      ├── SupervisorAgent")
    print("      ├── ScenarioAgent")
    print("      │   └── LLM Call (DeepSeek)")
    print("      │       ├── Prompt")
    print("      │       ├── Completion")
    print("      │       └── Token Usage")
    print("      ├── HumanApprovalAgent")
    print("      ├── TestCaseAgent")
    print("      │   └── LLM Call (DeepSeek)")
    print("      ├── EvaluationAgent")
    print("      │   └── LLM Call (DeepSeek)")
    print("      ├── HumanApprovalAgent")
    print("      ├── PlaywrightAgent")
    print("      │   └── LLM Call (DeepSeek)")
    print("      ├── ExecutionAgent")
    print("      └── ReportAgent")
    print("")
    print("   5. Click on each agent to see:")
    print("      - Execution duration")
    print("      - Input/output data")
    print("      - LLM prompts and responses")
    print("      - Token usage statistics")
    print("")
    print("=" * 80)
    
    # Show workflow logs
    if final_state.logs:
        print("\n📋 Workflow Logs (last 10):")
        for log in final_state.logs[-10:]:
            print(f"  {log}")
    
    print("\n" + "=" * 80)
    print("✅ SUCCESS - Check LangSmith dashboard now!")
    print("=" * 80)
    
except Exception as e:
    print("\n" + "=" * 80)
    print("❌ ERROR during workflow execution")
    print("=" * 80)
    print(f"\nError: {e}")
    import traceback
    traceback.print_exc()
    print("\n💡 Note: Even if execution failed, some traces may have been sent")
    print("   Check LangSmith dashboard to see which agents executed successfully")
    sys.exit(1)
