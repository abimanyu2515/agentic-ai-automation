import pytest
import os
import json
from uuid import uuid4
from backend.models.batch_context import BatchContext, QueueItem
from backend.services.artifact_manager import ArtifactManager
from backend.services.app_state_resolver import ApplicationStateResolver
from backend.services.navigation_planner import NavigationPlanner

def test_batch_context_and_queue_item():
    tc_id = uuid4()
    item = QueueItem(
        task_id="TASK-1",
        testcase_id=tc_id,
        required_state="SEARCH",
        dataset_row=0,
        priority=1,
        estimated_duration=10.5,
        retry_policy={"timeout": 1},
        status="queued"
    )
    assert item.task_id == "TASK-1"
    assert item.testcase_id == tc_id
    assert item.required_state == "SEARCH"

    context = BatchContext(
        batch_id="BATCH-20260715-001",
        project_id=uuid4(),
        queue=[item]
    )
    assert context.batch_id == "BATCH-20260715-001"
    assert len(context.queue) == 1
    assert context.queue[0].task_id == "TASK-1"

def test_artifact_manager():
    am = ArtifactManager()
    am.collect_screenshot("screenshot_1", "/path/to/shot1.png")
    am.collect_video("/path/to/video.webm")
    am.collect_trace("/path/to/trace.zip")
    am.collect_console("[log] Hello console")
    am.collect_logs("Running test step...")
    am.collect_timeline("Login click", "success", "user clicked login")

    payload = am.package()
    assert len(payload["screenshots"]) == 1
    assert payload["screenshots"][0]["name"] == "screenshot_1"
    assert payload["video"] == "/path/to/video.webm"
    assert payload["trace"] == "/path/to/trace.zip"
    assert payload["console"] == ["[log] Hello console"]
    assert payload["logs"] == ["Running test step..."]
    assert len(payload["timeline"]) == 1
    assert payload["timeline"][0]["event"] == "Login click"

def test_app_state_resolver(tmp_path):
    # Create a temporary config file
    config_data = {
        "application_states": [
            {
                "name": "LOGIN",
                "url_contains": "/login",
                "title_contains": "Login",
                "required_elements": ["#username", "#password"]
            },
            {
                "name": "SEARCH",
                "url_contains": "/search",
                "required_elements": ["#search-input"]
            }
        ]
    }
    config_file = tmp_path / "test_app_states.json"
    config_file.write_text(json.dumps(config_data))

    resolver = ApplicationStateResolver(config_path=str(config_file))
    
    # 1. Matches LOGIN
    state = resolver.resolve_state(
        url="https://site.com/login",
        title="Welcome - Login Page",
        visible_elements=["#username", "#password", "#logo"]
    )
    assert state == "LOGIN"

    # 2. Fails LOGIN due to missing title keyword
    state = resolver.resolve_state(
        url="https://site.com/login",
        title="Welcome Page",
        visible_elements=["#username", "#password"]
    )
    assert state == "UNKNOWN"

    # 3. Matches SEARCH
    state = resolver.resolve_state(
        url="https://site.com/search",
        title="Search Page",
        visible_elements=["#search-input"]
    )
    assert state == "SEARCH"

    # 4. Unknown page
    state = resolver.resolve_state(
        url="https://site.com/dashboard",
        title="Dashboard",
        visible_elements=[]
    )
    assert state == "UNKNOWN"

def test_navigation_planner():
    planner = NavigationPlanner()
    
    # LOGIN to SEARCH path
    path = planner.plan_navigation("LOGIN", "SEARCH")
    assert path == ["enter_credentials", "click_login"]

    # RESULTS to CONFIRMATION path via BOOKING
    path = planner.plan_navigation("RESULTS", "CONFIRMATION")
    assert path == ["select_hotel", "click_continue", "fill_billing_details", "click_book_now"]

    # Same state requires no action
    path = planner.plan_navigation("SEARCH", "SEARCH")
    assert path == []

    # Fallback to direct navigation for unknown states
    path = planner.plan_navigation("UNKNOWN", "LOGIN")
    assert path == ["navigate_direct"]

def test_recovery_manager():
    from backend.services.recovery_manager import RecoveryManager, RecoveryAction
    rm = RecoveryManager()
    
    # Assertion error -> FAIL
    action, retries = rm.get_recovery_action("Expected locator to be visible but was hidden", 0)
    assert action == RecoveryAction.FAIL
    assert retries == 0

    # Browser crash -> RESTART_BROWSER on 1st attempt
    action, retries = rm.get_recovery_action("browser disconnected or target closed", 0)
    assert action == RecoveryAction.RESTART_BROWSER
    assert retries == 1

    # Browser crash -> FAIL on 2nd attempt
    action, retries = rm.get_recovery_action("browser disconnected or target closed", 1)
    assert action == RecoveryAction.FAIL
    assert retries == 1

    # Auth expired -> RE_LOGIN on 1st attempt
    action, retries = rm.get_recovery_action("Authentication failed/expired", 0)
    assert action == RecoveryAction.RE_LOGIN
    assert retries == 1

    # Timeout -> RETRY on 1st attempt
    action, retries = rm.get_recovery_action("Timeout 30000ms exceeded", 0)
    assert action == RecoveryAction.RETRY
    assert retries == 1

    # Network error -> RETRY on 1st/2nd attempts, FAIL on 3rd
    action, retries = rm.get_recovery_action("net::ERR_CONNECTION_REFUSED", 0)
    assert action == RecoveryAction.RETRY
    assert retries == 1

    action, retries = rm.get_recovery_action("net::ERR_CONNECTION_REFUSED", 1)
    assert action == RecoveryAction.RETRY
    assert retries == 2

    action, retries = rm.get_recovery_action("net::ERR_CONNECTION_REFUSED", 2)
    assert action == RecoveryAction.FAIL
    assert retries == 2

def test_batch_queue_manager_execution():
    from backend.services.batch_queue_manager import BatchQueueManager
    from backend.models.batch_context import BatchContext, QueueItem
    from backend.services.playwright_runner import PlaywrightRunResult
    from backend.tests.conftest import FakePlaywrightRunner
    
    tc_id1 = uuid4()
    tc_id2 = uuid4()
    
    class FakeTestCase:
        def __init__(self, tc_id, script):
            self.id = tc_id
            self.playwright_script = script
            
    class FakeRepo:
        def __init__(self):
            self.db = {
                tc_id1: FakeTestCase(tc_id1, "await page.goto(BASE_URL);\nawait page.fill('#username', USERNAME);"),
                tc_id2: FakeTestCase(tc_id2, "await page.fill('#location', 'Sydney');")
            }
        def get_test_case(self, tc_id):
            return self.db.get(tc_id)
            
    runner = FakePlaywrightRunner(
        default_result=PlaywrightRunResult(
            status="passed",
            duration_seconds=1.2,
            screenshot_path="/path/to/img.png"
        )
    )
    
    item1 = QueueItem(
        task_id="TASK-1",
        testcase_id=tc_id1,
        required_state="LOGIN",
        status="queued"
    )
    item2 = QueueItem(
        task_id="TASK-2",
        testcase_id=tc_id2,
        required_state="SEARCH",
        status="queued"
    )
    
    context = BatchContext(
        batch_id="BATCH-TEST",
        project_id=uuid4(),
        queue=[item1, item2]
    )
    
    qm = BatchQueueManager(repo=FakeRepo(), runner=runner)
    result_context = qm.run_batch(context)
    
    assert result_context.passed == 2
    assert result_context.failed == 0
    assert result_context.current_state == "SEARCH"
    assert result_context.queue[0].status == "passed"
    assert result_context.queue[1].status == "passed"
    assert "TASK-1" in result_context.artifacts
    assert "TASK-2" in result_context.artifacts


