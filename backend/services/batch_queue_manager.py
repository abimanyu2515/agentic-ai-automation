import os
import time
from typing import Callable, Optional, Any
from uuid import UUID
from backend.models.batch_context import BatchContext, QueueItem
from backend.services.recovery_manager import RecoveryManager, RecoveryAction
from backend.services.app_state_resolver import ApplicationStateResolver
from backend.services.navigation_planner import NavigationPlanner
from backend.services.artifact_manager import ArtifactManager
from backend.services.execution_strategy import ExecutionStrategy, SequentialStrategy
from backend.models.execution_result import ExecutionResult, ExecutionStatus

class BatchQueueManager:
    def __init__(
        self,
        repo: Any = None,
        runner: Any = None,
        on_log: Optional[Callable[[str], None]] = None
    ):
        self.repo = repo
        self.runner = runner
        self.on_log = on_log
        self.recovery_manager = RecoveryManager()
        self.state_resolver = ApplicationStateResolver()
        self.navigation_planner = NavigationPlanner()

    def run_batch(self, context: BatchContext) -> BatchContext:
        if self.on_log:
            self.on_log(f"[QueueManager] Initiating batch run: {context.batch_id}")

        # Choose strategy based on BatchContext settings
        strategy = SequentialStrategy()
        # In the future, other strategies can be dynamically mapped here.

        # We will loop through the queue items sequentially
        for item in context.queue:
            if item.status != "queued":
                continue

            # Fetch the test case script to execute
            script = item.playwright_script
            if not script and self.repo:
                test_case = self.repo.get_test_case(item.testcase_id)
                if test_case:
                    script = getattr(test_case, "playwright_script", None)
            
            if not script:
                item.status = "skipped"
                context.skipped += 1
                if self.on_log:
                    self.on_log(f"[QueueManager] Skipped item {item.task_id} (No playwright script found)")
                continue

            item.status = "running"
            context.current_dataset_row = item.dataset_row
            context.current_retry_count = 0
            
            run_id = str(item.testcase_id)

            # Instantiate artifact manager for this item
            am = ArtifactManager()
            
            success = False
            while not success:
                if self.on_log:
                    self.on_log(f"[QueueManager] Executing {item.task_id} (Attempt {context.current_retry_count + 1})")

                # Calculate navigation decision
                nav_decision = self.navigation_planner.plan_navigation(context.current_state, item.required_state)
                context.navigation_decision = " -> ".join(nav_decision) if nav_decision else "NONE (Direct)"
                
                if self.on_log:
                    self.on_log(f"[QueueManager] Logical State: {context.current_state} | Target State: {item.required_state} | Decision: {context.navigation_decision}")

                # Configure storage state path for session sharing
                storage_state_path = os.path.abspath(
                    os.path.join("backend", "playwright", "artifacts", context.batch_id, "storage_state.json")
                )
                os.makedirs(os.path.dirname(storage_state_path), exist_ok=True)
                
                session_reused = os.path.exists(storage_state_path)
                if session_reused:
                    context.session_reuse_count += 1

                if session_reused:
                    # Inject conditional login to bypass redundant login steps
                    script = self._make_login_conditional(script)

                try:
                    # Run execution script through the Playwright runner, handling various mock/fake signatures
                    try:
                        raw_result = self.runner.run(
                            script,
                            run_id,
                            on_log=self.on_log,
                            storage_state_path=storage_state_path,
                            project_id=str(context.project_id)
                        )
                    except TypeError as t_err:
                        if "unexpected keyword argument" in str(t_err) or "got an unexpected" in str(t_err):
                            try:
                                raw_result = self.runner.run(
                                    script,
                                    run_id,
                                    on_log=self.on_log,
                                    storage_state_path=storage_state_path
                                )
                            except TypeError:
                                try:
                                    raw_result = self.runner.run(
                                        script,
                                        run_id,
                                        on_log=self.on_log
                                    )
                                except TypeError:
                                    raw_result = self.runner.run(
                                        script,
                                        run_id
                                    )
                        else:
                            raise
                except Exception as ex:
                    # If runner fails, build an error result
                    from backend.services.playwright_runner import PlaywrightRunResult
                    raw_result = PlaywrightRunResult(
                        status="error",
                        error_message=str(ex)
                    )

                # Collect artifacts into ArtifactManager
                if raw_result.screenshot_path:
                    am.collect_screenshot("Final Page State", raw_result.screenshot_path)
                if raw_result.video_path:
                    am.collect_video(raw_result.video_path)
                if raw_result.trace_path:
                    am.collect_trace(raw_result.trace_path)
                
                # Sync results and timeline events
                am.collect_logs(f"Step status: {raw_result.status}")
                if raw_result.error_message:
                    am.collect_logs(f"Error details: {raw_result.error_message}")
                
                # Extract timeline events from runner's captured stdout logs
                # The runner captures console.log('[Timeline] ...') messages in stdout
                if hasattr(raw_result, 'stdout_lines'):
                    for line in raw_result.stdout_lines:
                        if '[Timeline]' in line:
                            # Extract the timeline message
                            timeline_msg = line.split('[Timeline]', 1)[1].strip()
                            
                            # Determine event type based on content
                            event_type = "info"
                            if "error" in timeline_msg.lower() or "fail" in timeline_msg.lower():
                                event_type = "error"
                            elif "screenshot" in timeline_msg.lower():
                                event_type = "screenshot"
                            elif raw_result.status == "passed":
                                event_type = "success"
                            
                            am.collect_timeline(
                                timeline_msg,
                                event_type=event_type,
                                details=timeline_msg
                            )
                
                # If no timeline events were captured, add a fallback
                if not am.timeline:
                    am.collect_timeline(
                        f"Execution {item.task_id} {raw_result.status.capitalize()}",
                        event_type="success" if raw_result.status == "passed" else "error",
                        details=raw_result.error_message
                    )

                if raw_result.status == "passed":
                    item.status = "passed"
                    item.duration = raw_result.duration_seconds
                    context.passed += 1
                    success = True
                    context.current_state = item.required_state
                    if item.required_state == "LOGIN":
                        context.login_count += 1
                else:
                    # Check recovery rules and decide whether to retry
                    error_msg = raw_result.error_message or "Unknown execution error"
                    action, next_retry = self.recovery_manager.get_recovery_action(
                        error_msg,
                        context.current_retry_count
                    )
                    
                    if self.on_log:
                        self.on_log(f"[QueueManager] Execution failed. Recovery action: {action}")

                    if action == RecoveryAction.FAIL:
                        item.status = raw_result.status
                        item.error_message = error_msg
                        item.duration = raw_result.duration_seconds
                        context.failed += 1
                        success = True
                    elif action == RecoveryAction.RETRY:
                        context.current_retry_count = next_retry
                    elif action == RecoveryAction.RESTART_BROWSER or action == RecoveryAction.RE_LOGIN:
                        # Clear shared session data
                        if os.path.exists(storage_state_path):
                            try:
                                os.unlink(storage_state_path)
                            except OSError:
                                pass
                        context.current_retry_count = next_retry
                        context.current_state = "LOGIN"

            # Package and persist artifacts in context
            context.artifacts[item.task_id] = am.package()

        if self.on_log:
            self.on_log(f"[QueueManager] Batch execution complete. Passed: {context.passed}, Failed: {context.failed}")
        return context

    def _make_login_conditional(self, script: str) -> str:
        import re
        # Look for typical adactin login sequence matching both page.fill and page.locator().fill
        pattern = r"((?:console\.log\([^)]+\);\s*)?await\s+page\.(?:locator\([^)]+\)\.)?fill\(\s*['\"](?:input)?#username['\"].*?await\s+page\.(?:locator\([^)]+\)\.)?click\(\s*['\"](?:input)?#login['\"]\);?)"
        
        replacement = """\
  const is_logged_in = page.url().includes('SearchHotel.php') || (await page.$('#username').catch(() => null)) === null;
  if (!is_logged_in) {
    \\1
  } else {
    console.log('[Session Reuse] Already logged in. Bypassing login steps.');
  }"""
        
        modified = re.sub(pattern, replacement, script, flags=re.DOTALL | re.IGNORECASE)
        return modified
