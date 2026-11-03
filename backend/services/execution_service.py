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
