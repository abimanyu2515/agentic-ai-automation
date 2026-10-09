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
    DefectManagementAgent
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
from uuid import uuid4
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

    def __init__(self, runner: PlaywrightRunner | None = None, on_log = None, project_id = None):
        """
        Initialize execution agent with runner, logging callback, and project ID.
        
        Args:
            runner: PlaywrightRunner instance (injected for testing)
            on_log: Optional callback for real-time execution logging
            project_id: Optional UUID of the active project for credential retrieval
        """
        self.runner = runner or PlaywrightRunner()
        self.on_log = on_log
        self.project_id = project_id

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
        if not runnable:
            state.add_log(f"{self.name}: no approved test cases with a generated script to execute")
            return state

        state.add_log(f"{self.name} started")

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
                playwright_script=tc.playwright_script,
                dataset_row=i,
                priority=1,
                estimated_duration=30.0,
                status="queued"
            )
            queue.append(item)

        # Initialize BatchContext
        batch_id = f"BATCH-{datetime.date.today().strftime('%Y%m%d')}-{int(datetime.datetime.now().timestamp()) % 1000:03d}"
        context = BatchContext(
            batch_id=batch_id,
            project_id=self.project_id or uuid4(),
            queue=queue
        )

        # Execute using the queue manager
        qm = BatchQueueManager(runner=self.runner, on_log=self.on_log)
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
                trace_path=trace_path
            )
            # Attach timeline & console logs to the raw payload of ExecutionResult
            res._raw_payload = {
                "timeline": artifacts.get("timeline", []),
                "logs": artifacts.get("logs", []),
                "console": artifacts.get("console", [])
            }
            state.execution_results.append(res)

        passed = sum(1 for r in state.execution_results[-len(runnable):] if r.status == ExecutionStatus.PASSED)
        state.add_log(f"{self.name} finished: {passed}/{len(runnable)} passed")
        return state
