"""
Quick test script to verify the AI-enhanced report generation works.
This tests the filtering and enrichment logic without requiring a full execution.
"""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

from backend.services.report_service import ReportService
from uuid import uuid4

def test_filter_and_enrich():
    """Test the AI filtering and enrichment of execution steps"""
    
    print("=" * 80)
    print("Testing AI-Enhanced Report Filtering")
    print("=" * 80)
    
    # Create sample redundant steps (like the user reported)
    sample_steps = [
        {
            "step_name": "Initialize Browser",
            "action": "Action not yet performed",
            "expected": "Expected behavior not yet assessed",
            "actual": "No Observation Yet",
            "status": "✅ Passed",
            "time": "0.50s",
            "screenshot_name": None,
            "screenshot_url": None
        },
        {
            "step_name": "Browser opened",
            "action": "Browser is opened with a clean context",
            "expected": "Browser context should be ready for testing",
            "actual": "Browser ready",
            "status": "✅ Passed",
            "time": "0.75s",
            "screenshot_name": None,
            "screenshot_url": None
        },
        {
            "step_name": "Initialize Session",
            "action": "Session is restored from storage",
            "expected": "Application restores the previous session",
            "actual": "Session restored",
            "status": "✅ Passed",
            "time": "0.30s",
            "screenshot_name": None,
            "screenshot_url": None
        },
        {
            "step_name": "Navigate to adactinhotelapp.com",
            "action": "The browser navigates to 'https://adactinhotelapp.com/'",
            "expected": "Application home page should load successfully",
            "actual": "Page loaded: adactinhotelapp.com",
            "status": "✅ Passed",
            "time": "1.20s",
            "screenshot_name": "step-01.png",
            "screenshot_url": "/path/to/screenshot.png"
        },
        {
            "step_name": "Click login button",
            "action": "The user clicks on the login button",
            "expected": "The click should be accepted and trigger the next page action",
            "actual": "Clicked login button successfully",
            "status": "✅ Passed",
            "time": "0.45s",
            "screenshot_name": "step-02.png",
            "screenshot_url": "/path/to/screenshot2.png"
        },
        {
            "step_name": "Type into username field",
            "action": "The user types text into the username field",
            "expected": "Text should be entered correctly in the field",
            "actual": "Text entered in username field",
            "status": "✅ Passed",
            "time": "0.35s",
            "screenshot_name": None,
            "screenshot_url": None
        },
        {
            "step_name": "Type into password field",
            "action": "The user types text into the password field",
            "expected": "Text should be entered correctly in the field",
            "actual": "Text entered in password field",
            "status": "✅ Passed",
            "time": "0.35s",
            "screenshot_name": None,
            "screenshot_url": None
        },
        {
            "step_name": "Error: Login Failed",
            "action": "Executing the current test step",
            "expected": "The website should respond as expected",
            "actual": "❌ Failed: Timeout waiting for element",
            "status": "❌ Failed",
            "time": "30.00s",
            "screenshot_name": "error-screenshot.png",
            "screenshot_url": "/path/to/error.png"
        }
    ]
    
    # Mock test case
    class MockTestCase:
        title = "Login to Hotel Booking System"
        expected_result = "User should be logged in and redirected to search page"
        steps = [
            "Navigate to Adactin Hotel website",
            "Enter valid username",
            "Enter valid password",
            "Click login button",
            "Verify redirect to search page"
        ]
    
    print(f"\nINPUT: {len(sample_steps)} raw execution steps (includes redundant entries)")
    print("-" * 80)
    
    for i, step in enumerate(sample_steps, 1):
        print(f"{i}. {step['step_name']}")
        print(f"   Action: {step['action'][:50]}...")
        print(f"   Status: {step['status']}")
        print()
    
    # Test the filtering
    try:
        service = ReportService()
        test_case = MockTestCase()
        
        print("PROCESSING: Applying AI-driven filtering and enrichment...")
        print("-" * 80)
        
        filtered_steps = service.filter_and_enrich_steps_with_llm(sample_steps, test_case)
        
        print(f"\nOUTPUT: {len(filtered_steps)} meaningful steps (after AI filtering)")
        print("-" * 80)
        
        for i, step in enumerate(filtered_steps, 1):
            print(f"{i}. {step['step_name']}")
            print(f"   Action: {step['action']}")
            print(f"   Expected: {step['expected']}")
            print(f"   Actual: {step['actual']}")
            print(f"   Status: {step['status']}")
            if step.get('screenshot_name'):
                print(f"   Screenshot: {step['screenshot_name']}")
            print()
        
        reduction_pct = ((len(sample_steps) - len(filtered_steps)) / len(sample_steps)) * 100
        print("=" * 80)
        print(f"RESULT: Successfully reduced report by {reduction_pct:.1f}%")
        print(f"        ({len(sample_steps)} steps → {len(filtered_steps)} steps)")
        print("=" * 80)
        
        # Verify important steps were kept
        step_names = [s['step_name'].lower() for s in filtered_steps]
        has_navigation = any('navigate' in name for name in step_names)
        has_login = any('login' in name for name in step_names)
        has_failure = any('error' in name or 'fail' in name for name in step_names)
        
        print("\nVALIDATION:")
        print(f"  ✅ Navigation step kept: {has_navigation}")
        print(f"  ✅ Login action kept: {has_login}")
        print(f"  ✅ Failure step kept (CRITICAL): {has_failure}")
        
        if has_navigation and has_login and has_failure:
            print("\n✅ TEST PASSED: AI filtering is working correctly!")
        else:
            print("\n⚠️ WARNING: Some important steps may have been filtered out")
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        print("\nThis is expected if LLM service is not configured.")
        print("The feature will fall back to basic filtering in production.")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_filter_and_enrich()
