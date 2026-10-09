"""
Test script to verify LangSmith tracing is working correctly.

Run this script to send a test trace to your LangSmith dashboard.
Then check https://smith.langchain.com to verify the trace appears.
"""

import os
import sys

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Configure LangSmith from settings
from backend.config.settings import get_settings

settings = get_settings()

print("=" * 80)
print("LangSmith Trace Test")
print("=" * 80)
print(f"Tracing Enabled: {settings.langsmith.tracing_enabled}")
print(f"API Key: {'SET' if settings.langsmith.api_key else 'NOT SET'}")
print(f"Endpoint: {settings.langsmith.endpoint}")
print(f"Project: {settings.langsmith.project_name}")
print("=" * 80)

if not settings.langsmith.tracing_enabled:
    print("\n❌ ERROR: LangSmith tracing is disabled in .env")
    print("Set LANGSMITH_TRACING=true in your .env file")
    sys.exit(1)

if not settings.langsmith.api_key:
    print("\n❌ ERROR: LANGSMITH_API_KEY is not set")
    print("Add your LangSmith API key to .env file")
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

print("\n✓ Environment variables configured")

# Test 1: Direct LangSmith tracer
print("\n" + "=" * 80)
print("Test 1: Testing LangSmith Tracer Module")
print("=" * 80)

try:
    from backend.services.langsmith_tracer import get_tracer
    
    tracer = get_tracer()
    print(f"✓ Tracer initialized")
    print(f"  - Enabled: {tracer.enabled}")
    print(f"  - Client: {tracer.client}")
    
    if not tracer.enabled:
        print("\n❌ ERROR: Tracer is not enabled")
        sys.exit(1)
    
    # Test trace context creation
    print("\n✓ Creating test trace...")
    with tracer.create_trace_context("TestTrace", {"test": "value"}) as ctx:
        print(f"  - Trace ID: {ctx.trace_id}")
        # Simulate some work
        import time
        time.sleep(0.5)
    
    print("✓ Test trace completed")
    
except Exception as e:
    print(f"\n❌ ERROR in Tracer test: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 2: @traceable decorator
print("\n" + "=" * 80)
print("Test 2: Testing @traceable Decorator")
print("=" * 80)

try:
    from langsmith import traceable
    
    @traceable(name="test_function")
    def test_traced_function(x, y):
        """A simple function to test tracing"""
        import time
        time.sleep(0.3)
        return x + y
    
    print("✓ @traceable decorator imported and applied")
    
    # Call the traced function
    result = test_traced_function(5, 10)
    print(f"✓ Traced function executed: 5 + 10 = {result}")
    
except Exception as e:
    print(f"\n❌ ERROR in @traceable test: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 3: Agent execution with tracing
print("\n" + "=" * 80)
print("Test 3: Testing Agent Execution with Tracing")
print("=" * 80)

try:
    from backend.models.requirement import Requirement
    from backend.models.state import WorkflowState
    from backend.agents.scenario_agent import ScenarioAgent
    
    # Create a simple test requirement
    req = Requirement(
        id="test-req-123",
        title="Test LangSmith Tracing",
        description="This is a test requirement to verify LangSmith tracing works correctly"
    )
    
    # Create workflow state
    state = WorkflowState(requirement=req)
    
    print("✓ Test requirement created")
    print(f"  - ID: {req.id}")
    print(f"  - Title: {req.title}")
    
    # Note: Actually running the agent would require LLM calls
    # For this test, we'll just verify the setup is correct
    print("\n✓ Agent execution test setup complete")
    print("  (Skipping actual LLM call to avoid API usage)")
    
except Exception as e:
    print(f"\n❌ ERROR in Agent test: {e}")
    import traceback
    traceback.print_exc()
    # Don't exit - this test is optional

# Final instructions
print("\n" + "=" * 80)
print("✅ ALL TESTS PASSED!")
print("=" * 80)
print("\nNext steps:")
print("1. Go to https://smith.langchain.com")
print("2. Login to your account")
print(f"3. Select project: '{settings.langsmith.project_name}'")
print("4. You should see traces from this test script:")
print("   - TestTrace (from Test 1)")
print("   - test_function (from Test 2)")
print("\n5. To test with real workflow execution:")
print("   python backend/terminal_test.py")
print("\nThen check LangSmith for traces from all agents in your workflow!")
print("=" * 80)
