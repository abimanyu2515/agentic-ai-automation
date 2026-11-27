from types import SimpleNamespace
from uuid import uuid4

from backend.models.execution_result import ExecutionStatus
from backend.services.execution_service import ExecutionService


class _RunnerDouble:
    """Stands in for PlaywrightRunner; a class, so `PlaywrightRunner | None` annotations still work."""

    def __init__(self, timeout_seconds: int = 60, a11y_scan: bool = False):
        self.a11y_scan = a11y_scan


class BackgroundTaskCollector:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args):
        self.tasks.append((func, args))


def test_batch_run_uses_queue_manager_and_persists_each_result(monkeypatch):
    import backend.services.execution_service as execution_service
    import backend.services.batch_queue_manager as batch_queue_manager
    import backend.services.object_repository_harvest as object_repository_harvest
    import backend.services.object_repository_service as object_repository_service
    import backend.services.playwright_runner as playwright_runner
    import backend.services.report_service as report_service

    case = SimpleNamespace(
        id=uuid4(), title="Search for a product", playwright_script="saved script",
        scenario_id=uuid4(), status=None,
    )

    project_id = uuid4()
    project = SimpleNamespace(id=project_id, target_url="https://shop.example.com/", framework="playwright")

    class Repo:
        def __init__(self):
            self.updated = []
            self.results = []

        def get_project(self, requested_id):
            return project if requested_id == project_id else None

        def get_test_cases_for_project(self, project_id):
            return [case]

        def update_test_case(self, test_case):
            self.updated.append(test_case)

        def save_execution_result(self, project_id, result):
            self.results.append(result)

    repo = Repo()
    monkeypatch.setattr("backend.services.execution_service.get_project_repository", lambda: repo)
    monkeypatch.setattr(execution_service, "active_batch_events", {})
    monkeypatch.setattr(
        object_repository_service.ObjectRepositoryService,
        "renderer",
        lambda self: SimpleNamespace(render=lambda test_case: test_case.playwright_script),
    )
    monkeypatch.setattr(playwright_runner, "PlaywrightRunner", _RunnerDouble)
    monkeypatch.setattr(object_repository_harvest, "read_harvest_file", lambda path: [])
    monkeypatch.setattr(report_service.ReportService, "compile_reports_with_engine_selection", lambda *args: None)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_markers", lambda *args: 0)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_harvest", lambda *args: {})

    class QueueManager:
        def __init__(self, *, repo, runner, on_log):
            self.on_log = on_log

        def run_batch(self, context):
            # Spec 0001: every batch item runs with the snapshot taken at accept time.
            assert context.project_config["target_url"] == "https://shop.example.com"
            item = context.queue[0]
            assert item.playwright_script == "saved script"
            assert item.required_state == "SEARCH"
            item.status = "running"
            self.on_log("[OBJREPO] locator used")
            item.status = "passed"
            item.duration = 1.5
            context.passed = 1
            context.artifacts[item.task_id] = {"screenshots": [], "timeline": [], "logs": [], "console": []}
            return context

    monkeypatch.setattr(batch_queue_manager, "BatchQueueManager", QueueManager)
    tasks = BackgroundTaskCollector()
    batch_id = ExecutionService().start_batch_run(project_id, [case.id], background_tasks=tasks)

    assert len(tasks.tasks) == 1
    tasks.tasks[0][0]()

    assert len(repo.results) == 1
    assert repo.results[0].status == ExecutionStatus.PASSED
    # Spec 0012, AC-1: the batch id has its own field, and no cycle is invented.
    assert repo.results[0].batch_id == batch_id
    assert repo.results[0].test_cycle_id is None
    assert repo.results[0].project_config_snapshot == {
        "project_id": str(project_id),
        "target_url": "https://shop.example.com",
        "framework": "playwright",
        "credential_reference": str(project_id),
        # Spec 0011, AC-13: the project's accessibility settings, as strings.
        "a11y_enabled": "true",
        "a11y_gate_mode": "warn",
        "a11y_block_severity": "serious",
    }
    assert repo.updated == [case]
    assert execution_service.active_batch_events[str(batch_id)][-1]["status"] == "Completed"
    assert execution_service.active_batch_events[str(batch_id)][-1]["passed_count"] == 1


def test_batch_run_saved_copy_carries_batch_id_on_postgres_like_store(monkeypatch):
    """Spec 0012, AC-1, AC-6: PostgreSQL drops a test_cycle_id that is no real
    cycle, but the batch id lives in its own column, so the saved copy the
    accessibility step reads still has it."""
    import backend.services.execution_service as execution_service
    import backend.services.batch_queue_manager as batch_queue_manager
    import backend.services.object_repository_harvest as object_repository_harvest
    import backend.services.object_repository_service as object_repository_service
    import backend.services.playwright_runner as playwright_runner
    import backend.services.report_service as report_service

    case = SimpleNamespace(
        id=uuid4(), title="Search for a product", playwright_script="saved script",
        scenario_id=uuid4(), status=None,
    )
    project_id = uuid4()
    project = SimpleNamespace(id=project_id, target_url="https://shop.example.com/", framework="playwright")

    class PostgresLikeRepo:
        def __init__(self):
            self.saved = {}

        def get_project(self, requested_id):
            return project if requested_id == project_id else None

        def get_test_cases_for_project(self, project_id):
            return [case]

        def update_test_case(self, test_case):
            pass

        def save_execution_result(self, project_id, result):
            # The PostgreSQL guard: an unknown cycle id is saved as None.
            self.saved[result.id] = result.model_copy(update={"test_cycle_id": None})

        def get_execution_result(self, project_id, execution_id):
            return self.saved.get(execution_id)

    repo = PostgresLikeRepo()
    monkeypatch.setattr("backend.services.execution_service.get_project_repository", lambda: repo)
    monkeypatch.setattr(execution_service, "active_batch_events", {})
    monkeypatch.setattr(
        object_repository_service.ObjectRepositoryService,
        "renderer",
        lambda self: SimpleNamespace(render=lambda test_case: test_case.playwright_script),
    )
    monkeypatch.setattr(playwright_runner, "PlaywrightRunner", _RunnerDouble)
    monkeypatch.setattr(object_repository_harvest, "read_harvest_file", lambda path: [])
    monkeypatch.setattr(report_service.ReportService, "compile_reports_with_engine_selection", lambda *args: None)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_markers", lambda *args: 0)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_harvest", lambda *args: {})
    seen = []
    monkeypatch.setattr(
        ExecutionService, "apply_accessibility",
        lambda self, project_id, result, raw, **kwargs: seen.append(result.batch_id),
    )

    class QueueManager:
        def __init__(self, *, repo, runner, on_log):
            pass

        def run_batch(self, context):
            item = context.queue[0]
            item.status = "passed"
            item.duration = 1.0
            context.artifacts[item.task_id] = {"screenshots": [], "timeline": [], "logs": [], "console": []}
            return context

    monkeypatch.setattr(batch_queue_manager, "BatchQueueManager", QueueManager)
    tasks = BackgroundTaskCollector()
    batch_id = ExecutionService().start_batch_run(project_id, [case.id], background_tasks=tasks)
    tasks.tasks[0][0]()

    assert seen == [batch_id]


def test_webhook_run_saved_copy_carries_batch_id(monkeypatch):
    """Spec 0012, AC-5, AC-6: the webhook path reloads the saved run, and the
    accessibility step sees the batch id the payload sent."""
    import backend.services.execution_service as execution_service

    batch_id = uuid4()

    class PostgresLikeRepo:
        def __init__(self):
            self.saved = {}

        def save_execution_result(self, project_id, result):
            self.saved[result.id] = result.model_copy(update={"test_cycle_id": None})

        def get_execution_result(self, project_id, execution_id):
            return self.saved.get(execution_id)

        def get_test_case(self, test_case_id):
            return None

    monkeypatch.setattr(execution_service, "get_project_repository", lambda: PostgresLikeRepo())
    monkeypatch.setattr(execution_service, "emit_execution_completed", lambda *args: None)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_markers", lambda *args: 0)
    monkeypatch.setattr(
        ExecutionService, "_resolve_workspace_snapshot", lambda self, *args: {"a11y_gate_mode": "warn"}
    )
    seen = []
    monkeypatch.setattr(
        ExecutionService, "apply_accessibility",
        lambda self, project_id, result, raw, **kwargs: seen.append(result.batch_id),
    )

    ExecutionService().finalize_execution(
        uuid4(),
        {
            "execution_id": str(uuid4()),
            "test_case_id": str(uuid4()),
            "status": "passed",
            "batch_id": str(batch_id),
        },
    )

    assert seen == [batch_id]


# Spec 0006 task 12b (AC-14, AC-15): failed UI and batch runs join the knowledge index.

import pytest


def _capture_knowledge_queue(monkeypatch):
    import backend.services.knowledge_indexer as knowledge_indexer

    queued = []
    monkeypatch.setattr(
        knowledge_indexer,
        "queue_knowledge_source",
        lambda project_id, source_type, ref, remove=False: queued.append((project_id, source_type, ref, remove)),
    )
    return queued


@pytest.mark.parametrize(
    "status, expected_jobs",
    [("failed", 1), ("error", 1), ("timed_out", 1), ("passed", 0), ("skipped", 0)],
)
def test_finalize_execution_queues_failed_runs_for_knowledge(monkeypatch, status, expected_jobs):
    import backend.services.execution_service as execution_service

    queued = _capture_knowledge_queue(monkeypatch)
    order = []

    class Repo:
        def save_execution_result(self, project_id, result):
            order.append("save")

        def get_test_case(self, test_case_id):
            return None

    monkeypatch.setattr(execution_service, "get_project_repository", lambda: Repo())
    monkeypatch.setattr(execution_service, "emit_execution_completed", lambda *args: None)
    monkeypatch.setattr(
        ExecutionService, "apply_object_repository_markers", lambda *args: order.append("markers") or 0
    )

    project_id = uuid4()
    execution_id = uuid4()
    result = ExecutionService().finalize_execution(
        project_id,
        {
            "execution_id": str(execution_id),
            "test_case_id": str(uuid4()),
            "status": status,
            "error_message": "Locator not found",
        },
    )

    assert queued == [(project_id, "execution_failure", execution_id, False)] * expected_jobs
    assert result.id == execution_id
    assert order == ["save", "markers"]


def test_batch_run_queues_each_failed_item_for_knowledge(monkeypatch):
    import backend.services.execution_service as execution_service
    import backend.services.batch_queue_manager as batch_queue_manager
    import backend.services.object_repository_harvest as object_repository_harvest
    import backend.services.object_repository_service as object_repository_service
    import backend.services.playwright_runner as playwright_runner
    import backend.services.report_service as report_service

    queued = _capture_knowledge_queue(monkeypatch)
    project_id = uuid4()
    project = SimpleNamespace(id=project_id, target_url="https://shop.example.com/", framework="playwright")
    cases = [
        SimpleNamespace(id=uuid4(), title=title, playwright_script="script", scenario_id=uuid4(), status=None)
        for title in ("Checkout fails", "Login passes", "Search errors")
    ]

    class Repo:
        def __init__(self):
            self.results = []

        def get_project(self, requested_id):
            return project if requested_id == project_id else None

        def get_test_cases_for_project(self, project_id):
            return cases

        def update_test_case(self, test_case):
            pass

        def save_execution_result(self, project_id, result):
            self.results.append(result)

    repo = Repo()
    monkeypatch.setattr(execution_service, "get_project_repository", lambda: repo)
    monkeypatch.setattr(execution_service, "active_batch_events", {})
    monkeypatch.setattr(
        object_repository_service.ObjectRepositoryService,
        "renderer",
        lambda self: SimpleNamespace(render=lambda test_case: test_case.playwright_script),
    )
    monkeypatch.setattr(playwright_runner, "PlaywrightRunner", _RunnerDouble)
    monkeypatch.setattr(object_repository_harvest, "read_harvest_file", lambda path: [])
    monkeypatch.setattr(ExecutionService, "apply_object_repository_markers", lambda *args: 0)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_harvest", lambda *args: {})

    def reports(self, project_id, result):
        # A later step that raises must not cost an earlier item its entry.
        if result.status == ExecutionStatus.ERROR:
            raise RuntimeError("report engine down")

    monkeypatch.setattr(report_service.ReportService, "compile_reports_with_engine_selection", reports)

    class QueueManager:
        def __init__(self, *, repo, runner, on_log):
            pass

        def run_batch(self, context):
            for item, status in zip(context.queue, ("failed", "passed", "crashed")):
                item.status = status
                item.duration = 1.0
                item.error_message = None if status == "passed" else "boom"
                context.artifacts[item.task_id] = {}
            return context

    monkeypatch.setattr(batch_queue_manager, "BatchQueueManager", QueueManager)
    tasks = BackgroundTaskCollector()
    ExecutionService().start_batch_run(project_id, [case.id for case in cases], background_tasks=tasks)
    tasks.tasks[0][0]()

    statuses = [result.status for result in repo.results]
    assert statuses == [ExecutionStatus.FAILED, ExecutionStatus.PASSED, ExecutionStatus.ERROR]
    failed_ids = [result.id for result in repo.results if result.status != ExecutionStatus.PASSED]
    assert queued == [(project_id, "execution_failure", ref, False) for ref in failed_ids]
