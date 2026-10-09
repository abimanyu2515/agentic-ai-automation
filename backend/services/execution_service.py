from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Callable, Any

from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.test_case import TestCase, TestCaseStatus
from backend.repository.project_repository import get_project_repository

# Event Dispatcher for decoupling execution completion from report compilation
_execution_completed_listeners: list[Callable[[UUID, ExecutionResult], Any]] = []

def register_execution_completed_listener(listener: Callable[[UUID, ExecutionResult], Any]) -> None:
    """Registers a listener function to be called when an execution finishes."""
    if listener not in _execution_completed_listeners:
        _execution_completed_listeners.append(listener)

def emit_execution_completed(project_id: UUID, execution_result: ExecutionResult) -> None:
    """Dispatches execution completion events to all registered listeners."""
    for listener in _execution_completed_listeners:
        try:
            listener(project_id, execution_result)
        except Exception as err:
            import logging
            logging.error(f"[EventBus] Listener error on ExecutionCompleted: {err}")


class ExecutionService:
    @property
    def repo(self):
        return get_project_repository()

    def finalize_execution(self, project_id: UUID, payload: dict) -> ExecutionResult:
        """
        Processes the completed execution result, updates at-a-glance testcase statuses,
        persists the result record to repository storage, and dispatches the completed event.
        """
        import uuid
        
        exec_id = uuid.UUID(payload["execution_id"]) if "execution_id" in payload else uuid4()
        test_case_id_str = payload.get("test_case_id")
        if not test_case_id_str:
            test_case_id = UUID("00000000-0000-0000-0000-000000000000")
        else:
            try:
                test_case_id = uuid.UUID(test_case_id_str)
            except ValueError:
                test_case_id = UUID("00000000-0000-0000-0000-000000000000")
        
        status_str = payload.get("status", "error").lower()
        if status_str == "passed":
            status = ExecutionStatus.PASSED
        elif status_str == "failed":
            status = ExecutionStatus.FAILED
        elif status_str == "skipped":
            status = ExecutionStatus.SKIPPED
        else:
            status = ExecutionStatus.ERROR

        test_cycle_id_str = payload.get("test_cycle_id")
        test_cycle_id = None
        if test_cycle_id_str and test_cycle_id_str != "undefined" and test_cycle_id_str != "null":
            try:
                test_cycle_id = uuid.UUID(test_cycle_id_str)
            except ValueError:
                pass

        result = ExecutionResult(
            id=exec_id,
            test_case_id=test_case_id,
            status=status,
            duration_seconds=payload.get("duration_seconds", 0.0),
            error_message=payload.get("error_message"),
            screenshot_path=payload.get("screenshot_path"),
            video_path=payload.get("video_path"),
            trace_path=payload.get("trace_path"),
            browser_version=payload.get("browser_version"),
            executed_at=datetime.now(timezone.utc),
            test_cycle_id=test_cycle_id,
            timeline=payload.get("timeline", []),
            screenshots=payload.get("screenshots", [])
        )

        # Attach raw webhook payload so report listener can access timeline/screenshots
        result._raw_payload = payload

        # 1. Persist result
        self.repo.save_execution_result(project_id, result)

        # 2. Update at-a-glance run outcome status on the TestCase model.
        # Human approval and evaluation metadata fields remain completely IMMUTABLE.
        tc = self.repo.get_test_case(test_case_id)
        if tc:
            if status == ExecutionStatus.PASSED:
                tc.status = TestCaseStatus.PASSED
            elif status == ExecutionStatus.FAILED:
                tc.status = TestCaseStatus.FAILED
            else:
                tc.status = TestCaseStatus.BLOCKED
            self.repo.update_test_case(tc)

        # 3. Emit Execution Completed Event (handled asynchronously by ReportService)
        emit_execution_completed(project_id, result)

        return result
