"""
ExecutionAgent

Purpose:
    Orchestrates test execution by invoking PlaywrightRunner for approved test cases.
    Collects execution results, artifacts, and status updates.

Responsibilities:
    - Validate that approved test cases have generated Playwright scripts
    - Build execution queue with state dependencies and priorities
    - Invoke BatchQueueManager for parallel/sequential execution
    - Map execution results back to test case status
    - Collect artifacts (screenshots, videos, traces, console logs)
    - Append execution results to workflow state

Workflow Position:
    Dashboard/API
        ↓
    SupervisorAgent (validates preconditions)
        ↓
    ExecutionAgent
        ↓
    PlaywrightRunner/BatchQueueManager (subprocess execution)
        ↓
    ExecutionAnalysisAgent
        ↓
    BugReportingAgent
        ↓
    ReportAgent

Inputs:
    - state.approved_test_cases(): Test cases with evaluation_status=APPROVED or human-approved
    - test_case.playwright_script: Generated TypeScript/Python/Gherkin code
    - state.requirement: Context for logging

Outputs:
    - state.execution_results: List of ExecutionResult objects with status, duration, artifacts
    - test_case.status: Updated to PASSED, FAILED, or BLOCKED based on execution outcome

Dependencies:
    - PlaywrightRunner: Subprocess wrapper for npx playwright test
    - BatchQueueManager: Orchestrates multi-test execution with state management
    - BatchContext: Queue data structure with task metadata

Error Handling:
    - Catches PlaywrightRunnerError per test case to isolate failures
    - One test failure does not stop batch execution
    - Records error messages and marks status as BLOCKED on runner exceptions
"""

import datetime
from uuid import UUID, uuid4
from backend.agents.base import BaseAgent
from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase, TestCaseStatus
from backend.services.playwright_runner import PlaywrightRunner, PlaywrightRunnerError
from backend.models.batch_context import BatchContext, QueueItem
from backend.services.batch_queue_manager import BatchQueueManager

# ==========================================================
# Status Mapping Configuration
# ==========================================================

_MARKER_TAG = "[OBJREPO]"


class _MarkerCapturingRunner:
    """Thin runner proxy that keeps each call's `[OBJREPO]` lines (spec 0011).

    Runner calls run one after another, so lines seen while a call is in
    flight belong to that call's run_id (the test case id). Live lines come
    through `on_log`; a runner that never calls it (a test double) is read
    from `stdout_lines` instead. Lines of every attempt are kept.
    """

    def __init__(self, runner):
        self.runner = runner
        self.lines: dict[str, list[str]] = {}

    def run(self, script, run_id, *args, **kwargs):
        bucket = self.lines.setdefault(str(run_id), [])
        seen_live = []
        if "on_log" in kwargs:
            downstream = kwargs["on_log"]

            def tagged(line):
                if _MARKER_TAG in str(line):
                    seen_live.append(str(line))
                if downstream:
                    downstream(line)

            kwargs["on_log"] = tagged
        result = self.runner.run(script, run_id, *args, **kwargs)
        if seen_live:
            bucket.extend(seen_live)
        else:
            bucket.extend(
                str(line) for line in (getattr(result, "stdout_lines", None) or []) if _MARKER_TAG in str(line)
            )
        return result


_STATUS_MAP = {
    "passed": TestCaseStatus.PASSED,
    "failed": TestCaseStatus.FAILED,
    "skipped": TestCaseStatus.BLOCKED,
    "error": TestCaseStatus.BLOCKED,
}


class ExecutionAgent(BaseAgent):
    """
    Test execution orchestrator using Playwright Runner.
    
    This agent does not call LLMs. It coordinates subprocess execution
    and artifact collection.
    """
    
    name = "Execution Agent"

    def __init__(self, runner: PlaywrightRunner | None = None, on_log = None, project_id = None, repo = None, project_config: dict | None = None):
        """
        Initialize execution agent with runner, logging callback, and project ID.
        
        Args:
            runner: PlaywrightRunner instance (injected for testing)
            on_log: Optional callback for real-time execution logging
            project_id: Optional UUID of the active project for credential retrieval
            project_config: The project snapshot captured when the run was accepted
                (spec 0001). Sets the runner's TARGET_URL and is saved on every result.
        """
        self.runner = runner or PlaywrightRunner()
        self.on_log = on_log
        self.project_id = project_id
        self.repo = repo
        self.project_config = project_config

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Execute all approved test cases with Playwright scripts.
        
        Args:
            state: Workflow state containing approved test cases
            
        Returns:
            Updated state with execution_results populated
            
        Raises:
            ValueError: If state.requirement is None
            
        Execution Flow:
            1. Filter for approved test cases with scripts
            2. Build execution queue with state dependencies
            3. Create BatchContext with batch_id and queue
            4. Execute via BatchQueueManager
            5. Map results back to test case status
            6. Collect artifacts (screenshots, videos, traces, console logs)
            7. Append ExecutionResult objects to state.execution_results
        """
        if state.requirement is None:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        runnable = [tc for tc in state.approved_test_cases() if tc.playwright_script]
        # Spec 0011, AC-9: a heal rerun runs only the healed test cases, once.
        rerun_ids = list(state.heal_rerun_test_case_ids)
        is_rerun = bool(rerun_ids)
        if is_rerun:
            runnable = [tc for tc in runnable if tc.id in rerun_ids]
        if not runnable:
            state.heal_rerun_test_case_ids = []
            state.add_log(f"{self.name}: no approved test cases with a generated script to execute")
            return state

        state.add_log(f"{self.name} started" + (" (heal rerun, retries off)" if is_rerun else ""))
        # The id of the failed result each rerun replaces, read before the rerun appends.
        replaced = {r.test_case_id: r.id for r in state.latest_results()} if is_rerun else {}

        # Stored scripts hold object repository keys only; inject the current
        # locators so a locator fix reaches the next run without regeneration.
        from backend.services.object_repository_service import ObjectRepositoryService
        renderer = ObjectRepositoryService(project_repo=self.repo).renderer()

        # Compile queue items
        queue = []
        for i, tc in enumerate(runnable):
            required_state = "LOGIN"
            title_lower = tc.title.lower()
            if "search" in title_lower:
                required_state = "SEARCH"
            elif "select" in title_lower or "result" in title_lower:
                required_state = "RESULTS"
            elif "book" in title_lower:
                required_state = "BOOKING"
            elif "confirm" in title_lower:
                required_state = "CONFIRMATION"

            item = QueueItem(
                task_id=f"TASK-{i+1}",
                requirement_id=state.requirement.id,
                testcase_id=tc.id,
                required_state=required_state,
                playwright_script=renderer.render(tc),
                dataset_row=i,
                priority=1,
                estimated_duration=30.0,
                status="queued"
            )
            queue.append(item)

        # Initialize BatchContext. A heal rerun reuses the first run's batch id,
        # so the saved login session (storage state) is reused too.
        batch_id = state.execution_batch_id if is_rerun and state.execution_batch_id else (
            f"BATCH-{datetime.date.today().strftime('%Y%m%d')}-{int(datetime.datetime.now().timestamp()) % 1000:03d}"
        )
        if state.execution_batch_id is None:
            state.execution_batch_id = batch_id
        context = BatchContext(
            batch_id=batch_id,
            project_id=self.project_id or uuid4(),
            queue=queue,
            project_config=self.project_config,
        )

        # Execute using the queue manager
        runner = _MarkerCapturingRunner(self.runner)
        qm = BatchQueueManager(runner=runner, on_log=self.on_log, retries=not is_rerun)
        result_context = qm.run_batch(context)

        # Adapt result_context back to workflow state
        for item in result_context.queue:
            tc = next((t for t in runnable if t.id == item.testcase_id), None)
            if not tc:
                continue

            tc.status = _STATUS_MAP.get(item.status, TestCaseStatus.BLOCKED)

            artifacts = result_context.artifacts.get(item.task_id, {})
            screenshot_path = artifacts["screenshots"][0]["path"] if artifacts.get("screenshots") else None
            video_path = artifacts.get("video")
            trace_path = artifacts.get("trace")

            error_message = item.error_message
            if not error_message and item.status in ["failed", "error"]:
                error_message = "Test execution failed."

            # Extract first trace/video/screenshot path
            res = ExecutionResult(
                test_case_id=tc.id,
                status=ExecutionStatus(item.status) if item.status in ["passed", "failed"] else ExecutionStatus.ERROR,
                duration_seconds=item.duration,
                error_message=error_message,
                screenshot_path=screenshot_path,
                video_path=video_path,
                trace_path=trace_path,
                project_config_snapshot=self.project_config,
            )
            # Attach timeline & console logs to the raw payload of ExecutionResult
            res._raw_payload = {
                "timeline": artifacts.get("timeline", []),
                "logs": artifacts.get("logs", []),
                "console": artifacts.get("console", [])
            }
            if is_rerun:
                res.heal_rerun_of = replaced.get(tc.id)
                res.heal_ids = [
                    UUID(str(heal["heal_id"]))
                    for heal in state.heal_results
                    if heal.get("status") == "applied" and str(tc.id) in {str(t) for t in heal.get("test_case_ids", [])}
                ] + [
                    # Spec 0011, AC-21: a script repair rerun also tests the script heal.
                    UUID(str(heal["script_heal_id"]))
                    for heal in state.script_heal_results
                    if heal.get("status") == "applied" and str(heal.get("test_case_id")) == str(tc.id)
                    and not heal.get("verified")
                ]
            state.run_marker_lines[str(res.id)] = list(runner.lines.get(str(tc.id), []))
            state.execution_results.append(res)

        if is_rerun:
            state.heal_rerun_test_case_ids = []

        passed = sum(1 for r in state.execution_results[-len(runnable):] if r.status == ExecutionStatus.PASSED)
        state.add_log(f"{self.name} finished: {passed}/{len(runnable)} passed")
        return state
