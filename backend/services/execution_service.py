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


_A11Y_GATE_MODES = ("off", "warn", "block")
_A11Y_BLOCK_SEVERITIES = ("critical", "serious")


class ExecutionService:
    def __init__(self, analysis_service=None, bug_report_service=None, accessibility_service=None):
        self._analysis_service = analysis_service
        self._bug_report_service = bug_report_service
        self._accessibility_service = accessibility_service

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

    @property
    def accessibility_service(self):
        """Builds and gates accessibility reports, as AccessibilityAgent does in graph runs."""
        if self._accessibility_service is None:
            from backend.services.accessibility_service import AccessibilityService

            self._accessibility_service = AccessibilityService(project_repo=self.repo)
        return self._accessibility_service

    def apply_accessibility(self, project_id: UUID, result: ExecutionResult, raw, *, enabled: bool | None = None,
                            test_case=None, background_tasks=None):
        """Spec 0011, AC-1, AC-2, AC-6: the batch and workspace twin of
        AccessibilityAgent plus BugReportingAgent's accessibility loop. Builds,
        gates, and saves the run's report, then drafts its bugs. Pass the saved
        result, so the gate uses the stored snapshot. Never raises.

        With `background_tasks` the report is saved and gated with the axe text
        now, and the LLM explanation and the bug drafts (which quote it) run
        after the response, so a webhook never waits on the LLM."""
        try:
            report = self.accessibility_service.process(
                project_id, result, raw, enabled=enabled, enrich=background_tasks is None,
            )
        except Exception:
            logger.exception("Accessibility processing failed: execution_id=%s", result.id)
            return None
        if background_tasks is not None:
            background_tasks.add_task(self._enrich_and_draft_accessibility, project_id, report, result, test_case)
            return report
        # draft_from_accessibility never raises and only drafts for a succeeded scan.
        self.bug_report_service.draft_from_accessibility(project_id, report, result, test_case)
        return report

    def _enrich_and_draft_accessibility(self, project_id: UUID, report, result: ExecutionResult, test_case) -> None:
        """The after response half of `apply_accessibility`. Never raises."""
        report = self.accessibility_service.enrich_saved(report, result)
        self.bug_report_service.draft_from_accessibility(project_id, report, result, test_case)

    def _resolve_workspace_snapshot(self, project_id: UUID, exec_id: UUID, payload: dict) -> dict | None:
        """Spec 0011, *Workspace run settings* step 3: the snapshot of a workspace
        run, which has no backend side start. A saved snapshot wins; otherwise
        it is built from the project now, with the engine's echoed gate settings
        overlaid. None when the project no longer exists."""
        from backend.services.project_configuration import ProjectConfigurationError, capture_project_snapshot

        try:
            saved = self.repo.get_execution_result(project_id, exec_id)
        except Exception:
            logger.warning("Saved execution could not be read, building its snapshot: execution_id=%s", exec_id)
            self._rollback()
            saved = None
        if saved is not None and saved.project_config_snapshot:
            return dict(saved.project_config_snapshot)

        try:
            project = self.repo.get_project(project_id)
        except Exception:
            logger.warning("Project could not be read for the run snapshot: project_id=%s", project_id)
            self._rollback()
            project = None
        if project is None:
            return None
        try:
            snapshot = capture_project_snapshot(project)
        except ProjectConfigurationError:
            logger.warning("Project not ready at finalize, snapshot taken without checks: project_id=%s", project_id)
            snapshot = capture_project_snapshot(project, require_ready=False)

        echo = payload.get("a11y_settings")
        if not isinstance(echo, dict):
            return snapshot
        # Only exact values replace the project's; `enabled` is never written here.
        for field, key, allowed in (
            ("gate_mode", "a11y_gate_mode", _A11Y_GATE_MODES),
            ("block_severity", "a11y_block_severity", _A11Y_BLOCK_SEVERITIES),
        ):
            value = echo.get(field)
            if isinstance(value, str) and value in allowed:
                snapshot[key] = value
            else:
                logger.warning("a11y_settings field ignored: %s", field)
        return snapshot

    def _rollback(self) -> None:
        try:
            self.repo.rollback()
        except Exception:
            logger.debug("Repository rollback failed", exc_info=True)

    @staticmethod
    def _echoed_scan_decision(payload: dict, execution_id) -> bool | None:
        """AC-8, step 6 and 7: the engine's own scan decision, or None to use the snapshot."""
        echo = payload.get("a11y_settings")
        if isinstance(echo, dict):
            enabled = echo.get("enabled")
            if isinstance(enabled, bool):
                return enabled
            logger.warning("a11y_settings field ignored: enabled")
            return None
        error = payload.get("a11y_settings_error")
        if error:
            logger.warning("a11y settings read failed in engine: %s (execution_id=%s)", error, execution_id)
        else:
            logger.warning("no a11y_settings in completed webhook (older engine?): execution_id=%s", execution_id)
        return None

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
        test_cycle_id: UUID | None = None,
    ) -> UUID:
        """Run the existing sequential batch workflow and expose its event stream.

        Spec 0012: every run of the batch is saved with the returned `batch_id`,
        and with `test_cycle_id` when the caller checked a real cycle (AC-1, AC-2)."""
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
        # Spec 0012, AC-12, AC-13: the log is never empty and names its project,
        # so the gate can answer for a batch with no saved run yet.
        events: list[dict] = [
            {"status": "Started", "project_id": str(project_id), "log": "Initializing batch queue runner..."}
        ]
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
                # Spec 0011, AC-1: batch runs scan like graph runs, from the run snapshot.
                runner = PlaywrightRunner(a11y_scan=self.accessibility_service.scan_enabled(project_config))
                manager = BatchQueueManager(repo=self.repo, runner=runner, on_log=on_runner_log)
                manager.run_batch(context)

                from backend.agents.execution_agent import _STATUS_MAP
                from backend.models.test_case import TestCaseStatus
                from backend.services.accessibility_service import read_a11y_file
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
                        test_cycle_id=test_cycle_id,
                        batch_id=batch_id,
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
                    # Spec 0011: the saved copy carries the stored snapshot. A skipped
                    # item never ran, so its folder may hold an older run's a11y.json.
                    saved = self._saved_or(project_id, result)
                    raw = None if result.status == ExecutionStatus.SKIPPED else read_a11y_file(
                        ARTIFACTS_ROOT / str(item.testcase_id)
                    )
                    self.apply_accessibility(project_id, saved, raw, test_case=case)
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

    def _saved_or(self, project_id: UUID, result: ExecutionResult) -> ExecutionResult:
        """The persisted copy of a just saved result, or the result itself if the read fails."""
        try:
            saved = self.repo.get_execution_result(project_id, result.id)
        except Exception:
            logger.warning("Saved execution could not be read back: execution_id=%s", result.id)
            self._rollback()
            saved = None
        return result if saved is None else saved

    @staticmethod
    def batch_log_state(project_id: UUID, batch_id: UUID | str) -> str | None:
        """Spec 0012, AC-7, AC-12, AC-13: `running` or `ended` for this project's
        backend batch log, or None when there is no log (a workspace batch, or a
        batch from before a restart) or the log belongs to another project."""
        events = active_batch_events.get(str(batch_id))
        if not events or events[0].get("project_id") != str(project_id):
            return None
        return "ended" if events[-1].get("status") in ("Completed", "Error") else "running"

    @staticmethod
    def batch_in_progress(project_id: UUID, batch_id: UUID | str) -> bool:
        """Spec 0012, AC-7: whether this process is still running this project's batch."""
        return ExecutionService.batch_log_state(project_id, batch_id) == "running"

    def cycle_in_project(self, project_id: UUID, cycle_id: UUID) -> bool:
        """Whether a test cycle exists and its release belongs to this project."""
        try:
            cycle = self.repo.get_test_cycle(cycle_id)
            if cycle is None:
                return False
            release = self.repo.get_release(cycle.release_id)
        except Exception:
            logger.warning("Test cycle could not be read: cycle_id=%s", cycle_id)
            self._rollback()
            return False
        return release is not None and release.project_id == project_id

    def _saved_batch_id(self, project_id: UUID, exec_id: UUID) -> UUID | None:
        """The batch id a run was saved with first, if any (spec 0012, AC-5)."""
        try:
            saved = self.repo.get_execution_result(project_id, exec_id)
        except Exception:
            logger.warning("Saved execution could not be read for its batch id: execution_id=%s", exec_id)
            self._rollback()
            return None
        return saved.batch_id if saved is not None else None

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

    def harvest_secrets(self, project_id: UUID, test_case_id: UUID | None) -> list:
        """Values redacted from harvested elements: vault credentials and test data.

        Self healing (spec 0011, AC-2) redacts candidates with the same list.
        """
        import logging

        from backend.services import object_repository_harvest as harvest

        secrets: list = []
        try:
            from backend.services.vault_service import VaultService

            secrets.extend(VaultService().get_credentials(str(project_id)))
        except Exception:
            logging.warning("Harvest redaction could not load vault credentials: project_id=%s", project_id)
        try:
            test_case = self.repo.get_test_case(test_case_id) if test_case_id else None
        except Exception:
            test_case = None
        secrets.extend(harvest.values_from_test_data(test_case))
        return secrets

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
            secrets = self.harvest_secrets(project_id, test_case_id)

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

        # Spec 0012, AC-5: a test_cycle_id must be a real cycle of this project,
        # on both stores. Anything else is saved as null with a warning.
        test_cycle_id_str = payload.get("test_cycle_id")
        test_cycle_id = None
        if test_cycle_id_str and test_cycle_id_str != "undefined" and test_cycle_id_str != "null":
            try:
                test_cycle_id = uuid.UUID(str(test_cycle_id_str))
            except ValueError:
                test_cycle_id = None
            if test_cycle_id is None or not self.cycle_in_project(project_id, test_cycle_id):
                logger.warning(
                    "Webhook test_cycle_id dropped, no such cycle in this project: execution_id=%s test_cycle_id=%s",
                    exec_id, test_cycle_id_str,
                )
                test_cycle_id = None

        # Spec 0012, AC-5: an invalid batch_id is saved as null, and a batch id
        # saved first is kept when the webhook repeats.
        batch_id = None
        batch_id_raw = payload.get("batch_id")
        if batch_id_raw:
            try:
                batch_id = uuid.UUID(str(batch_id_raw))
            except ValueError:
                logger.warning(
                    "Webhook batch_id dropped, not a UUID: execution_id=%s batch_id=%r", exec_id, batch_id_raw,
                )
        saved_batch_id = self._saved_batch_id(project_id, exec_id)
        if saved_batch_id is not None:
            batch_id = saved_batch_id

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
            batch_id=batch_id,
            timeline=payload.get("timeline", []),
            screenshots=payload.get("screenshots", [])
        )

        # Attach raw webhook payload so report listener can access timeline/screenshots
        result._raw_payload = payload

        # Spec 0011: a workspace run has no backend side start, so its snapshot is
        # resolved here, before triage and bug drafting read it. A repeated
        # webhook reuses the snapshot saved the first time.
        result.project_config_snapshot = self._resolve_workspace_snapshot(project_id, exec_id, payload)

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

        tc = self.repo.get_test_case(test_case_id)

        # 1d. Accessibility report and its bugs (spec 0011), from the persisted
        #     copy so the gate always uses the snapshot that was stored first.
        if result.project_config_snapshot is None:
            logger.warning(
                "Project gone, accessibility processing skipped: project_id=%s execution_id=%s",
                project_id, exec_id,
            )
        else:
            saved = self._saved_or(project_id, result)
            saved._raw_payload = payload
            self.apply_accessibility(
                project_id,
                saved,
                payload.get("a11y"),
                enabled=self._echoed_scan_decision(payload, exec_id),
                test_case=tc,
                background_tasks=background_tasks,
            )
            result = saved

        # 2. Update at-a-glance run outcome status on the TestCase model.
        # Human approval and evaluation metadata fields remain completely IMMUTABLE.
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
