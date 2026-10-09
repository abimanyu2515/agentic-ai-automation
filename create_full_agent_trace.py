"""
Create a Full Agent Trace in LangSmith

This script triggers a complete workflow that activates all agents,
creating a unified trace in LangSmith showing the entire agent hierarchy.

The trace will show:
- SupervisorAgent
- ScenarioAgent (with LLM calls)
- HumanApprovalAgent
- TestCaseAgent (with LLM calls)
- EvaluationAgent (with LLM calls)
- PlaywrightAgent (with LLM calls)
- ExecutionAgent
- ReportAgent

After running this script, check LangSmith to see the complete trace!
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
print("Creating Full Agent Trace in LangSmith")
print("=" * 80)

# Import workflow components
from backend.models.requirement import Requirement
from backend.graph.workflow import run_workflow

# Create a simple test requirement
print("\n✓ Creating test requirement...")
requirement = Requirement(
    title="Book Hotel Room - LangSmith Trace Test",
    description=(
        "As a registered user, I want to book a hotel room using the Adactin Hotel App "
        "so that I can secure accommodation for my travel.\n\n"
        "Acceptance Criteria:\n"
        "- User can search for available hotels\n"
        "- User can select a hotel from search results\n"
        "- User can complete the booking with payment details\n"
        "- System displays booking confirmation with order number"
    )
)

print(f"  - Title: {requirement.title}")
print(f"  - ID: {requirement.id}")

# Run the workflow
# NOTE: This will make real LLM calls and cost API credits
# But it will create a complete trace in LangSmith!

print("\n" + "=" * 80)
print("Running Workflow - This will activate all agents!")
print("=" * 80)
print("\nNOTE: This makes real LLM calls to generate scenarios and test cases.")
print("Check your LangSmith dashboard to see the trace being created in real-time!")
print("\nStarting workflow...\n")

try:
    # Run the complete workflow
    final_state = run_workflow(requirement)
    
    print("\n" + "=" * 80)
    print("✅ WORKFLOW COMPLETED SUCCESSFULLY!")
    print("=" * 80)
    
    # Display summary
    print("\nWorkflow Summary:")
    print(f"  - Scenarios Generated: {len(final_state.generated_scenarios)}")
    print(f"  - Test Cases Generated: {len(final_state.generated_test_cases)}")
    
    if final_state.approved_test_cases():
        print(f"  - Approved Test Cases: {len(final_state.approved_test_cases())}")
    
    # Display logs
    print("\nWorkflow Logs:")
    for log in final_state.logs:
        print(f"  {log}")
    
    print("\n" + "=" * 80)
    print("🎉 SUCCESS! Check LangSmith for the Complete Trace!")
    print("=" * 80)
    print("\nTo view the trace:")
    print("1. Go to: https://smith.langchain.com")
    print("2. Select project: 'My Project'")
    print("3. Look for the most recent 'LangGraph' trace")
    print("4. Expand to see all agents:")
    print("   - SupervisorAgent")
    print("   - ScenarioAgent")
    print("     └─ LLM Call (scenario generation)")
    print("   - HumanApprovalAgent (auto-approval)")
    print("   - TestCaseAgent")
    print("     └─ LLM Call (test case generation)")
    print("   - EvaluationAgent")
    print("     └─ LLM Call (evaluation)")
    print("   - HumanApprovalAgent (auto-approval)")
    print("   - PlaywrightAgent")
    print("     └─ LLM Call (script generation)")
    print("   - ExecutionAgent")
    print("   - ReportAgent")
    print("\n" + "=" * 80)
    
except KeyboardInterrupt:
    print("\n\n⚠️  Workflow interrupted by user")
    print("Partial trace may be visible in LangSmith")
    sys.exit(1)
    
except Exception as e:
    print(f"\n\n❌ ERROR during workflow execution:")
    print(f"   {e}")
    print("\nPartial trace may still be visible in LangSmith")
    
    import traceback
    traceback.print_exc()
    sys.exit(1)
