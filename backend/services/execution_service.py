from datetime import datetime, timezone
import logging
from uuid import UUID, uuid4
from typing import Callable, Any

from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.test_case import TestCase, TestCaseStatus
from backend.repository.project_repository import get_project_repository

logger = logging.getLogger(__name__)

# Live event logs of batch runs, keyed by batch ID. The batch stream route and
# the chat assistant's status refresh read them. In memory, per process.
active_batch_events: dict[str, list[dict]] = {}

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
    def __init__(self, analysis_service=None, bug_report_service=None):
        self._analysis_service = analysis_service
        self._bug_report_service = bug_report_service

    @property
    def repo(self):
        return get_project_repository()

    @property
    def analysis_service(self):
        """The log analyser shared with ExecutionAnalysisAgent. Inject a stub in tests."""
        if self._analysis_service is None:
            from backend.services.log_analysis_service import LogAnalysisService

            self._analysis_service = LogAnalysisService(repo=self.repo)
        return self._analysis_service

    @property
    def bug_report_service(self):
        """Drafts bug reports for Product Bug failures, as BugReportingAgent does in graph runs."""
        if self._bug_report_service is None:
            from backend.services.bug_report_service import BugReportService

            self._bug_report_service = BugReportService(project_repo=self.repo)
        return self._bug_report_service

    def _triage(self, project_id: UUID, result: ExecutionResult, test_case=None) -> None:
        """Run the log analyser on a failed result before it is saved, then draft a
        bug report for a Product Bug. Never raises, so a triage problem can't change
        a run's outcome or fail a batch or webhook."""
        if getattr(result.status, "value", result.status) in (
            ExecutionStatus.PASSED.value, ExecutionStatus.SKIPPED.value,
        ):
            return
        try:
            self.analysis_service.analyze(project_id, result, test_case)
        except Exception:
            logger.exception("Log analysis failed: execution_id=%s", result.id)
            return
        # draft_from_execution never raises and skips anything but a Product Bug.
        self.bug_report_service.draft_from_execution(project_id, result, test_case)

    def _capture_snapshot(self, project_id: UUID) -> dict[str, str]:
        """The project's configuration at accept time (spec 0001). Raises
        ProjectConfigurationError("PROJECT_NOT_READY") when it is incomplete."""
        from backend.services.project_configuration import capture_project_snapshot

        project = self.repo.get_project(project_id)
        if project is None:
            raise ValueError(f"Project {project_id} not found")
        return capture_project_snapshot(project)

    def start_test_case_run(
        self, project_id: UUID, test_case_id: UUID, background_tasks=None, workflow_service=None,
    ) -> UUID:
        """Start the existing single-case execution flow and return its execution ID."""
        from threading import Thread
        from uuid import uuid4

        from backend.services.workflow_service import active_execution_events, run_execution_and_stream
        if self.repo.get_test_case(test_case_id) is None:
            raise ValueError(f"Test case {test_case_id} not found")
        # Spec 0001, AC-3 and AC-4: refuse an unready project before anything is
        # queued, and capture the configuration this run must use.
        project_config = self._capture_snapshot(project_id)
        execution_id = uuid4()
        # Reset the test case's event log before queueing, so a reader never
        # mistakes the previous run's "Completed" event for this run's.
        active_execution_events[str(test_case_id)] = []
        if workflow_service is None:
            from backend.services.workflow_service import WorkflowService

            workflow_service = WorkflowService()
        if background_tasks is not None:
            background_tasks.add_task(
                run_execution_and_stream, workflow_service, project_id, test_case_id, execution_id, background_tasks,
                project_config,
            )
        else:
            Thread(
                target=run_execution_and_stream,
                args=(workflow_service, project_id, test_case_id, execution_id, None, project_config),
                name=f"chat-execution-{execution_id}",
                daemon=True,
            ).start()
        return execution_id

    def start_batch_run(
        self,
        project_id: UUID,
        test_case_ids: list[UUID],
        background_tasks=None,
        *,
        enforce_project_scope: bool = True,
    ) -> UUID:
        """Run the existing sequential batch workflow and expose its event stream."""
        from threading import Thread
        from backend.models.batch_context import BatchContext, QueueItem
        from backend.models.execution_result import ExecutionStatus
        from backend.services.batch_queue_manager import BatchQueueManager
        from backend.services.object_repository_service import ObjectRepositoryService
        from backend.services.playwright_runner import PlaywrightRunner

        if enforce_project_scope:
            selected = {case.id: case for case in self.repo.get_test_cases_for_project(project_id)}
            requested = [selected.get(case_id) for case_id in test_case_ids]
        else:
            requested = [self.repo.get_test_case(case_id) for case_id in test_case_ids]
            selected = {case.id: case for case in requested if case is not None}
        runnable = [case for case in requested if case and case.playwright_script]
        if not runnable:
            raise ValueError("No runnable test cases selected")
        project_config = self._capture_snapshot(project_id)

        batch_id = uuid4()
        batch_id_text = str(batch_id)
        events: list[dict] = []
        active_batch_events[batch_id_text] = events
        renderer = ObjectRepositoryService(project_repo=self.repo).renderer()
        queue = []
        for index, case in enumerate(runnable):
            title = case.title.lower()
            required_state = "LOGIN"
            if "search" in title:
                required_state = "SEARCH"
            elif "select" in title or "result" in title:
                required_state = "RESULTS"
            elif "book" in title:
                required_state = "BOOKING"
            elif "confirm" in title:
                required_state = "CONFIRMATION"
            queue.append(QueueItem(
                task_id=f"TASK-{index + 1}",
                testcase_id=case.id,
                required_state=required_state,
                playwright_script=renderer.render(case),
                dataset_row=index,
                priority=1,
                estimated_duration=30.0,
            ))
        context = BatchContext(
            batch_id=batch_id_text, project_id=project_id, queue=queue, project_config=project_config,
        )
        events.append({"status": "Started", "log": "Initializing batch queue runner..."})

        def append_event(status: str, message: str, current_item=None) -> None:
            total = len(context.queue)
            completed = context.passed + context.failed + context.skipped
            remaining = total - completed
            events.append({
                "status": status,
                "log": message,
                "progress": int(completed / total * 100) if total else 0,
                "total_tasks": total,
                "remaining_tasks": remaining,
                "passed_count": context.passed,
                "failed_count": context.failed,
                "skipped_count": context.skipped,
                "current_state": context.current_state,
                "next_required_state": current_item.required_state if current_item else "N/A",
                "navigation_decision": context.navigation_decision or "N/A",
                "current_retry": context.current_retry_count,
                "current_dataset_row": context.current_dataset_row if context.current_dataset_row is not None else 0,
                "etr": f"{remaining * 30}s" if remaining else "0s",
            })

        def run() -> None:
            marker_lines: dict[str, list[str]] = {}

            def schedule(callback, *args, name: str) -> None:
                if background_tasks is not None:
                    background_tasks.add_task(callback, *args)
                else:
                    Thread(target=callback, args=args, name=name, daemon=True).start()

            def on_runner_log(line):
                active_item = next((item for item in context.queue if item.status == "running"), None)
                if active_item and "[OBJREPO]" in str(line):
                    marker_lines.setdefault(active_item.task_id, []).append(str(line))
                append_event("Running", str(line), active_item)

            try:
                from backend.services.jira_sync_service import sync_execution_started

                for case in runnable:
                    schedule(
                        sync_execution_started,
                        case.scenario_id,
                        name=f"batch-jira-start-{case.id}",
                    )
                manager = BatchQueueManager(repo=self.repo, runner=PlaywrightRunner(), on_log=on_runner_log)
                manager.run_batch(context)

                from backend.agents.execution_agent import _STATUS_MAP
                from backend.models.test_case import TestCaseStatus
                from backend.services.object_repository_harvest import read_harvest_file
                from backend.services.playwright_runner import ARTIFACTS_ROOT
                from backend.services.report_service import ReportService

                report_service = ReportService(repo=self.repo)
                for item in context.queue:
                    case = selected[item.testcase_id]
                    case.status = _STATUS_MAP.get(item.status, TestCaseStatus.BLOCKED)
                    self.repo.update_test_case(case)
                    artifacts = context.artifacts.get(item.task_id, {})
                    screenshot = artifacts.get("screenshots") or []
                    result = ExecutionResult(
                        test_case_id=case.id,
                        test_cycle_id=batch_id,
                        status=(
                            ExecutionStatus(item.status)
                            if item.status in {status.value for status in ExecutionStatus}
                            else ExecutionStatus.ERROR
                        ),
                        duration_seconds=item.duration,
                        error_message=item.error_message,
                        screenshot_path=screenshot[0].get("path") if screenshot else None,
                        video_path=artifacts.get("video"),
                        trace_path=artifacts.get("trace"),
                        project_config_snapshot=project_config,
                        timeline=artifacts.get("timeline", []),
                    )
                    result._raw_payload = {
                        "timeline": artifacts.get("timeline", []),
                        "logs": artifacts.get("logs", []),
                        "console": artifacts.get("console", []),
                    }
                    if result.status not in (ExecutionStatus.PASSED, ExecutionStatus.SKIPPED):
                        append_event("Running", f"Analysing failure logs for {case.title}...", item)
                        self._triage(project_id, result, case)
                        append_event(
                            "Running",
                            f"Triage complete for {case.title}: {result.failure_category or 'unknown'}",
                            item,
                        )
                    self.repo.save_execution_result(project_id, result)
                    self._queue_failure_knowledge(project_id, result)
                    self.apply_object_repository_markers(
                        project_id, result.id, marker_lines.get(item.task_id, [])
                    )
                    self.apply_object_repository_harvest(
                        project_id,
                        case.id,
                        result.id,
                        read_harvest_file(ARTIFACTS_ROOT / str(item.testcase_id)),
                    )
                    report_service.compile_reports_with_engine_selection(project_id, result)

                    from backend.services.jira_sync_service import sync_execution_finished

                    schedule(
                        sync_execution_finished,
                        case.scenario_id,
                        result.id,
                        name=f"batch-jira-sync-{result.id}",
                    )
                append_event("Completed", "Batch execution complete.")
            except Exception as error:
                logger.exception("Batch execution failed: batch_id=%s", batch_id_text)
                append_event("Error", f"Batch execution failed: {error}")

        if background_tasks is not None:
            background_tasks.add_task(run)
        else:
            Thread(target=run, name=f"chat-batch-{batch_id_text}", daemon=True).start()
        return batch_id

    @staticmethod
    def _queue_failure_knowledge(project_id: UUID, result: ExecutionResult) -> None:
        """Queue a failed or errored run for the knowledge index (spec 0006, AC-14, AC-15).

        Call it right after the result is saved. It only enqueues and never raises.
        Imported lazily to avoid an import cycle with the indexer."""
        from backend.services.knowledge_indexer import queue_knowledge_source
        from backend.services.knowledge_sources import FAILED_EXECUTION_STATUSES

        if getattr(result.status, "value", result.status) in FAILED_EXECUTION_STATUSES:
            queue_knowledge_source(project_id, "execution_failure", result.id)

    def apply_object_repository_markers(self, project_id: UUID, execution_id: UUID, lines: list[str]) -> int:
        """The single place run markers become element and locator health (spec 0004, AC-10).

        finalize_execution passes the workspace `completed` payload's logs; the
        Python runner paths pass the lines they collected from the runner. It
        never raises, so a bad marker can't fail a webhook or a run.
        """
        from backend.services.object_repository_service import ObjectRepositoryService

        return ObjectRepositoryService(project_repo=self.repo).apply_run_markers(project_id, execution_id, lines)

    def apply_object_repository_harvest(self, project_id: UUID, test_case_id: UUID | None, execution_id: UUID, snapshots) -> dict:
        """The single place harvested pages are ingested (spec 0004, AC-18 to AC-20).

        Keeps the first snapshot per path up to harvest_max_pages, redacts the
        project's vault credentials and the test case's test data, then merges
        each page with the reconnaissance rules minus marking missing. Failed
        runs are ingested too. It never raises, so a bad snapshot can't fail a
        webhook or a run.
        """
        import logging

        from backend.services import object_repository_harvest as harvest
        from backend.services.object_repository_service import ObjectRepositoryService, _settings

        totals = {"pages": 0, "added": 0, "updated": 0}
        try:
            settings = _settings()
            if not settings.harvest_enabled:
                return totals
            pages = harvest.parse_snapshots(snapshots, settings.harvest_max_pages)
            if not pages:
                return totals
            secrets: list = []
            try:
                from backend.services.vault_service import VaultService

                secrets.extend(VaultService().get_credentials(str(project_id)))
            except Exception:
                logging.warning("Harvest redaction could not load vault credentials: project_id=%s", project_id)
            test_case = self.repo.get_test_case(test_case_id) if test_case_id else None
            secrets.extend(harvest.values_from_test_data(test_case))

            service = ObjectRepositoryService(project_repo=self.repo)
            for path, elements in pages:
                try:
                    counts = service.ingest_recon(
                        project_id, path, harvest.clean_elements(elements, secrets), mark_missing=False
                    )
                except Exception:
                    logging.exception("Harvest ingest failed for one page: project_id=%s path=%s", project_id, path)
                    continue
                totals["pages"] += 1
                totals["added"] += counts["added"]
                totals["updated"] += counts["updated"]
            project = self.repo.get_project(project_id)
            logging.info(
                "Object repository harvest: project_id=%s execution_id=%s safe_test_environment=%s pages=%s added=%s updated=%s",
                project_id,
                execution_id,
                bool(project and project.is_safe_test_environment),
                totals["pages"],
                totals["added"],
                totals["updated"],
            )
        except Exception:
            logging.exception("Object repository harvest failed: execution_id=%s", execution_id)
        return totals

    def triage_saved_execution(self, project_id: UUID, execution_id: UUID) -> None:
        """Triage an already saved failed run and save the triage fields.

        The webhook route runs this as a background task, so the workspace gets
        its response without waiting for the analysis LLM. It re-reads the
        result first, so it only adds triage to what other listeners saved."""
        try:
            result = self.repo.get_execution_result(project_id, execution_id)
            if result is None:
                return
            try:
                case = self.repo.get_test_case(result.test_case_id)
            except Exception:
                case = None
            self._triage(project_id, result, case)
            self.repo.save_execution_result(project_id, result)
        except Exception:
            logger.exception("Background log analysis failed: execution_id=%s", execution_id)

    def finalize_execution(self, project_id: UUID, payload: dict, background_tasks=None) -> ExecutionResult:
        """
        Processes the completed execution result, updates at-a-glance testcase statuses,
        persists the result record to repository storage, and dispatches the completed event.

        A failed run is triaged by the log analyser. With `background_tasks` the
        triage runs after the response (see triage_saved_execution); without
        it, triage runs inline before the result is saved.
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

        # 0. Triage a failed run with the log analyser: inline before saving, or
        #    after the response when the caller passes background tasks.
        failed = status not in (ExecutionStatus.PASSED, ExecutionStatus.SKIPPED)
        if failed and background_tasks is not None:
            background_tasks.add_task(self.triage_saved_execution, project_id, exec_id)
        elif failed:
            try:
                case = self.repo.get_test_case(test_case_id)
            except Exception:
                case = None
            self._triage(project_id, result, case)

        # 1. Persist result
        self.repo.save_execution_result(project_id, result)
        self._queue_failure_knowledge(project_id, result)

        # 1b. Element and locator health from the run's [OBJREPO] markers.
        self.apply_object_repository_markers(project_id, exec_id, payload.get("logs") or [])

        # 1c. Pages the run visited, harvested by the workspace engine.
        if payload.get("harvest") is not None:
            self.apply_object_repository_harvest(project_id, result.test_case_id, exec_id, payload.get("harvest"))

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
