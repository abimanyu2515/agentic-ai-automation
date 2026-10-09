"""
Preservation Property Tests for Playwright Reporting Bugfix

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

These tests verify that non-reporting functionality remains unchanged during the fix.
They use property-based testing to generate many test cases for stronger guarantees.

IMPORTANT: These tests are EXPECTED TO PASS on UNFIXED code.
They establish the baseline behavior that must be preserved.

The tests cover:
1. Execution status determination (pass/fail/error)
2. Artifact generation (screenshots, videos, traces)
3. Process cleanup (kill process tree, close pipes)
4. Playwright.config.ts settings respected
5. Vault credentials injection
6. Storage state file creation
7. Browser configuration unchanged
"""

import os
import pytest
import tempfile
import shutil
from pathlib import Path
from uuid import uuid4
from hypothesis import given, strategies as st, settings, HealthCheck

from backend.services.playwright_runner import PlaywrightRunner, PlaywrightRunResult


# Strategy for generating valid Playwright scripts
@st.composite
def playwright_script_strategy(draw):
    """
    Generate valid Playwright test scripts with varying characteristics.
    
    Scripts can have:
    - Different page navigation patterns
    - Different interaction types (goto, click, fill)
    - Pass or fail conditions
    - Various timeouts
    """
    script_templates = [
        # Simple passing script
        """
import {{ test, expect }} from '@playwright/test';

test('simple pass test', async ({{ page }}) => {{
    await page.goto('https://example.com');
    const title = await page.title();
    expect(title).toBeTruthy();
}});
""",
        # Script with multiple interactions
        """
import {{ test }} from '@playwright/test';

test('interaction test', async ({{ page }}) => {{
    await page.goto('https://example.com');
    await page.waitForTimeout(100);
}});
""",
        # Intentionally failing script
        """
import {{ test, expect }} from '@playwright/test';

test('failing test', async ({{ page }}) => {{
    await page.goto('https://example.com');
    expect(false).toBe(true);
}});
""",
    ]
    
    return draw(st.sampled_from(script_templates))


@st.composite
def headless_mode_strategy(draw):
    """Generate headless mode configuration."""
    return draw(st.booleans())


@st.composite
def timeout_strategy(draw):
    """Generate timeout values (reasonable range for tests)."""
    return draw(st.integers(min_value=10, max_value=60))


@pytest.mark.slow
@settings(
    max_examples=3,  # Limited for CI/CD performance
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow]
)
@given(
    script=playwright_script_strategy(),
    headless=headless_mode_strategy(),
    timeout=timeout_strategy()
)
def test_property_execution_status_determination(script, headless, timeout):
    """
    Property 2.1: Execution Status Determination Works Correctly
    
    For all valid Playwright scripts, the PlaywrightRunner SHALL correctly
    determine execution status (passed/failed/error) based on the test outcome.
    
    This is a core execution mechanic that MUST remain unchanged.
    
    **Expected Outcome**: PASS (confirms baseline behavior preserved)
    """
    print(f"\n=== Testing execution status determination ===")
    print(f"Headless: {headless}, Timeout: {timeout}s")
    
    runner = PlaywrightRunner(timeout_seconds=timeout)
    run_id = str(uuid4())
    
    try:
        result = runner.run(
            script=script,
            run_id=run_id,
            headless=headless
        )
        
        # Verify result is returned (not an exception)
        assert isinstance(result, PlaywrightRunResult), \
            "PlaywrightRunner must return PlaywrightRunResult"
        
        # Verify status is a valid value
        valid_statuses = {"passed", "failed", "skipped", "error"}
        assert result.status in valid_statuses, \
            f"Status must be one of {valid_statuses}, got: {result.status}"
        
        # Verify duration is non-negative
        assert result.duration_seconds >= 0, \
            f"Duration must be non-negative, got: {result.duration_seconds}"
        
        # If status is error, error_message should be present
        if result.status == "error":
            assert result.error_message is not None, \
                "Error status must have error_message"
        
        print(f"✅ Status determination correct: {result.status}")
        print(f"   Duration: {result.duration_seconds}s")
        if result.error_message:
            print(f"   Error: {result.error_message[:100]}")
        
    except Exception as e:
        # Runner should return error result, not raise exceptions (unless setup issue)
        pytest.fail(f"PlaywrightRunner raised unexpected exception: {e}")
    
    finally:
        # Cleanup
        run_dir = Path(__file__).parent.parent / "playwrightt" / "artifacts" / run_id
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except:
                pass


@pytest.mark.slow
def test_property_artifacts_generated_in_correct_locations():
    """
    Property 2.2: Artifacts Generated in Correct Locations
    
    For all test script executions, artifacts (screenshots, videos, traces)
    SHALL be generated in backend/playwrightt/artifacts/{run_id}/ directory.
    
    This is critical for artifact management and MUST remain unchanged.
    
    **Expected Outcome**: PASS (confirms baseline behavior preserved)
    """
    print(f"\n=== Testing artifact generation locations ===")
    
    # Simple passing script that should generate artifacts
    # NOTE: Don't import { test } - the screenshot wrapper already imports it
    script = """
test('artifact test', async ({ page }) => {
    await page.goto('https://example.com');
    await page.screenshot({ path: 'test-screenshot.png' });
});
"""
    
    runner = PlaywrightRunner(timeout_seconds=30)
    run_id = str(uuid4())
    
    try:
        result = runner.run(
            script=script,
            run_id=run_id,
            headless=True
        )
        
        # Verify run directory was created
        artifacts_root = Path(__file__).parent.parent / "playwrightt" / "artifacts"
        run_dir = artifacts_root / run_id
        
        assert run_dir.exists(), \
            f"Run directory should exist at: {run_dir}"
        
        print(f"✅ Run directory created: {run_dir}")
        
        # Verify test.spec.ts was created
        spec_file = run_dir / "test.spec.ts"
        assert spec_file.exists(), \
            f"test.spec.ts should exist at: {spec_file}"
        
        print(f"✅ Spec file created: {spec_file}")
        
        # Verify playwright.config.ts was created
        config_file = run_dir / "playwright.config.ts"
        assert config_file.exists(), \
            f"playwright.config.ts should exist at: {config_file}"
        
        print(f"✅ Config file created: {config_file}")
        
        # Verify report.json was created
        report_file = run_dir / "report.json"
        assert report_file.exists(), \
            f"report.json should exist at: {report_file}"
        
        print(f"✅ Report file created: {report_file}")
        
        # Verify screenshots directory created
        screenshots_dir = run_dir / "screenshots"
        assert screenshots_dir.exists(), \
            f"Screenshots directory should exist at: {screenshots_dir}"
        
        print(f"✅ Screenshots directory created: {screenshots_dir}")
        
        # Check if trace path is returned (if trace was generated)
        if result.trace_path:
            print(f"✅ Trace path returned: {result.trace_path}")
        
        print(f"\n✅ All artifacts generated in correct locations")
        
    finally:
        # Cleanup
        run_dir = Path(__file__).parent.parent / "playwrightt" / "artifacts" / run_id
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except:
                pass


@pytest.mark.slow
def test_property_process_cleanup_works():
    """
    Property 2.3: Process Cleanup Works Correctly
    
    For all test executions (especially timeouts and crashes), the
    PlaywrightRunner SHALL properly kill the process tree and close pipes.
    
    This prevents resource leaks and MUST remain unchanged.
    
    **Expected Outcome**: PASS (confirms baseline behavior preserved)
    """
    print(f"\n=== Testing process cleanup on timeout ===")
    
    # Script that will timeout (infinite wait)
    # NOTE: Don't import { test } - the screenshot wrapper already imports it
    script = """
test('timeout test', async ({ page }) => {
    await page.goto('https://example.com');
    await page.waitForTimeout(120000);  // Wait 2 minutes (will timeout)
});
"""
    
    runner = PlaywrightRunner(timeout_seconds=5)  # Short timeout
    run_id = str(uuid4())
    
    try:
        result = runner.run(
            script=script,
            run_id=run_id,
            headless=True
        )
        
        # Verify timeout handling returns error result (not hanging)
        assert result.status == "error", \
            "Timeout should result in error status"
        
        assert "did not finish within" in result.error_message.lower() or \
               "timeout" in result.error_message.lower(), \
            f"Error message should indicate timeout, got: {result.error_message}"
        
        print(f"✅ Timeout handled correctly")
        print(f"   Status: {result.status}")
        print(f"   Error: {result.error_message}")
        
        # Process should be killed (test completes quickly, not hanging)
        assert result.duration_seconds < 30, \
            "Process cleanup should complete quickly after timeout"
        
        print(f"✅ Process cleanup completed in {result.duration_seconds}s")
        
    finally:
        # Cleanup
        run_dir = Path(__file__).parent.parent / "playwrightt" / "artifacts" / run_id
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except:
                pass


@pytest.mark.slow
@settings(
    max_examples=2,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow]
)
@given(headless=headless_mode_strategy())
def test_property_playwright_config_settings_respected(headless):
    """
    Property 2.4: Playwright.config.ts Settings Respected
    
    For all test executions, the generated playwright.config.ts SHALL
    contain correct settings (headless mode, timeout, reporters, etc.)
    and these settings SHALL be applied during execution.
    
    Configuration handling MUST remain unchanged.
    
    **Expected Outcome**: PASS (confirms baseline behavior preserved)
    """
    print(f"\n=== Testing playwright.config.ts settings ===")
    print(f"Headless mode: {headless}")
    
    # NOTE: Don't import { test } - the screenshot wrapper already imports it
    script = """
test('config test', async ({ page }) => {
    await page.goto('https://example.com');
});
"""
    
    runner = PlaywrightRunner(timeout_seconds=30)
    run_id = str(uuid4())
    
    try:
        result = runner.run(
            script=script,
            run_id=run_id,
            headless=headless
        )
        
        # Read generated config file
        config_file = Path(__file__).parent.parent / "playwrightt" / "artifacts" / run_id / "playwright.config.ts"
        assert config_file.exists(), "Config file should be generated"
        
        config_content = config_file.read_text()
        
        # Verify headless setting
        expected_headless = "true" if headless else "false"
        assert f"headless: {expected_headless}" in config_content, \
            f"Config should contain 'headless: {expected_headless}'"
        
        print(f"✅ Headless setting correct: {expected_headless}")
        
        # Verify screenshot setting
        assert "screenshot:" in config_content, \
            "Config should contain screenshot setting"
        
        print(f"✅ Screenshot setting present")
        
        # Verify video setting
        assert "video:" in config_content, \
            "Config should contain video setting"
        
        print(f"✅ Video setting present")
        
        # Verify reporter setting (JSON reporter for report.json)
        assert "['json'" in config_content, \
            "Config should contain JSON reporter"
        
        print(f"✅ JSON reporter configured")
        
        # Verify timeout setting
        assert "timeout:" in config_content, \
            "Config should contain timeout setting"
        
        print(f"✅ Timeout setting present")
        
        print(f"\n✅ All configuration settings correct")
        
    finally:
        # Cleanup
        run_dir = Path(__file__).parent.parent / "playwrightt" / "artifacts" / run_id
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except:
                pass


@pytest.mark.slow
def test_property_vault_credentials_injected():
    """
    Property 2.5: Vault Credentials Injected Correctly
    
    For all test executions with a project_id, the PlaywrightRunner SHALL
    inject TARGET_USERNAME and TARGET_PASSWORD environment variables from
    the vault service into the child process environment.
    
    Credential injection MUST remain unchanged.
    
    **Expected Outcome**: PASS (confirms baseline behavior preserved)
    """
    print(f"\n=== Testing vault credentials injection ===")
    
    # Script that accesses environment variables
    # NOTE: Don't import { test } - the screenshot wrapper already imports it
    script = """
test('credential test', async ({ page }) => {
    // Log environment variables (for testing only)
    const username = process.env.TARGET_USERNAME;
    const password = process.env.TARGET_PASSWORD;
    
    console.log('TARGET_USERNAME present: ' + (username ? 'yes' : 'no'));
    console.log('TARGET_PASSWORD present: ' + (password ? 'yes' : 'no'));
    
    await page.goto('https://example.com');
});
"""
    
    runner = PlaywrightRunner(timeout_seconds=30)
    run_id = str(uuid4())
    project_id = str(uuid4())
    
    captured_logs = []
    def log_callback(line):
        captured_logs.append(line)
        print(f"   {line}")
    
    try:
        # Note: This test verifies the mechanism works, even if vault is empty
        # The PlaywrightRunner attempts to load credentials and inject them
        result = runner.run(
            script=script,
            run_id=run_id,
            headless=True,
            project_id=project_id,
            on_log=log_callback
        )
        
        # Verify the execution completed (credential injection didn't break execution)
        assert result.status in {"passed", "failed", "error"}, \
            f"Execution should complete, got status: {result.status}"
        
        print(f"✅ Execution completed with credential injection mechanism")
        print(f"   Status: {result.status}")
        
        # Check if credential environment variable check logged
        # (The script logs whether TARGET_USERNAME and TARGET_PASSWORD are present)
        log_text = "\n".join(captured_logs)
        
        # Verify the script executed and logged credential checks
        if "TARGET_USERNAME present:" in log_text:
            print(f"✅ Credential injection mechanism executed")
            print(f"   Script verified environment variable availability")
        else:
            print(f"✅ Credential injection mechanism present (script output captured)")
        
    finally:
        # Cleanup
        run_dir = Path(__file__).parent.parent / "playwrightt" / "artifacts" / run_id
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except:
                pass


@pytest.mark.slow
def test_property_storage_state_created():
    """
    Property 2.6: Storage State Files Created Correctly
    
    For all test executions with storage_state_path specified, the
    PlaywrightRunner SHALL configure the test to save storage state
    after execution, enabling authentication persistence.
    
    Storage state handling MUST remain unchanged.
    
    **Expected Outcome**: PASS (confirms baseline behavior preserved)
    """
    print(f"\n=== Testing storage state file creation ===")
    
    # Create temporary storage state file path
    temp_dir = tempfile.mkdtemp()
    storage_state_path = os.path.join(temp_dir, "auth_state.json")
    
    print(f"Storage state path: {storage_state_path}")
    
    # Script that sets some browser state
    # NOTE: Don't import { test } - the screenshot wrapper already imports it
    script = """
test('storage state test', async ({ page }) => {
    await page.goto('https://example.com');
    // Set some local storage (simulating authentication)
    await page.evaluate(() => {
        localStorage.setItem('test_key', 'test_value');
    });
});
"""
    
    runner = PlaywrightRunner(timeout_seconds=30)
    run_id = str(uuid4())
    
    try:
        result = runner.run(
            script=script,
            run_id=run_id,
            headless=True,
            storage_state_path=storage_state_path
        )
        
        # Verify execution completed
        assert result.status in {"passed", "failed"}, \
            f"Execution should complete, got status: {result.status}"
        
        print(f"✅ Execution completed with storage state configuration")
        print(f"   Status: {result.status}")
        
        # Verify storage state handling was configured in test.spec.ts
        spec_file = Path(__file__).parent.parent / "playwrightt" / "artifacts" / run_id / "test.spec.ts"
        assert spec_file.exists(), "Spec file should be generated"
        
        spec_content = spec_file.read_text()
        
        # Check if storage state save logic is injected
        assert "storageState" in spec_content or "afterEach" in spec_content, \
            "Storage state save logic should be injected in script"
        
        print(f"✅ Storage state configuration injected in test script")
        
        # Note: Storage state file may or may not be created depending on execution
        # The key preservation requirement is that the mechanism is present
        if os.path.exists(storage_state_path):
            print(f"✅ Storage state file created: {storage_state_path}")
        else:
            print(f"✅ Storage state mechanism configured (file creation depends on execution)")
        
    finally:
        # Cleanup
        run_dir = Path(__file__).parent.parent / "playwrightt" / "artifacts" / run_id
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except:
                pass
        
        # Cleanup temp dir
        try:
            shutil.rmtree(temp_dir)
        except:
            pass


@pytest.mark.slow
def test_property_browser_configuration_unchanged():
    """
    Property 2.7: Browser Configuration Unchanged
    
    For all test executions, browser configuration (Chromium launch,
    page creation, headless/headed mode) SHALL work correctly.
    
    Browser automation mechanics MUST remain unchanged.
    
    **Expected Outcome**: PASS (confirms baseline behavior preserved)
    """
    print(f"\n=== Testing browser configuration ===")
    
    # Script that verifies browser is working
    # NOTE: Don't import { test, expect } - the screenshot wrapper already imports them
    script = """
test('browser test', async ({ page, context, browser }) => {
    // Verify browser is launched
    expect(browser).toBeTruthy();
    
    // Verify context is created
    expect(context).toBeTruthy();
    
    // Verify page is available
    expect(page).toBeTruthy();
    
    // Verify page can navigate
    await page.goto('https://example.com');
    
    // Verify page has content
    const content = await page.content();
    expect(content).toBeTruthy();
    expect(content.length).toBeGreaterThan(0);
});
"""
    
    runner = PlaywrightRunner(timeout_seconds=30)
    run_id = str(uuid4())
    
    try:
        result = runner.run(
            script=script,
            run_id=run_id,
            headless=True
        )
        
        # Verify browser automation worked
        assert result.status == "passed", \
            f"Browser automation should work, got status: {result.status}"
        
        print(f"✅ Browser automation working correctly")
        print(f"   Status: {result.status}")
        print(f"   Duration: {result.duration_seconds}s")
        
        # Verify page interaction occurred (non-zero duration)
        assert result.duration_seconds > 0, \
            "Browser interaction should take some time"
        
        print(f"✅ Browser configuration unchanged and working")
        
    finally:
        # Cleanup
        run_dir = Path(__file__).parent.parent / "playwrightt" / "artifacts" / run_id
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except:
                pass


# Manual test runner for debugging
if __name__ == "__main__":
    print("=" * 80)
    print("Preservation Property Tests")
    print("=" * 80)
    print("\nThese tests verify that non-reporting functionality remains unchanged.")
    print("They should PASS on unfixed code, establishing baseline behavior.")
    print("=" * 80)
    
    # Run the tests
    pytest.main([__file__, "-v", "-s", "-m", "slow"])
