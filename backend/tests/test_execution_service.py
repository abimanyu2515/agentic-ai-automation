from types import SimpleNamespace
from uuid import uuid4

from backend.models.execution_result import ExecutionStatus
from backend.services.execution_service import ExecutionService


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

    class Repo:
        def __init__(self):
            self.updated = []
            self.results = []

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
    monkeypatch.setattr(playwright_runner, "PlaywrightRunner", lambda: object())
    monkeypatch.setattr(object_repository_harvest, "read_harvest_file", lambda path: [])
    monkeypatch.setattr(report_service.ReportService, "compile_reports_with_engine_selection", lambda *args: None)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_markers", lambda *args: 0)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_harvest", lambda *args: {})

    class QueueManager:
        def __init__(self, *, repo, runner, on_log):
            self.on_log = on_log

        def run_batch(self, context):
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
    batch_id = ExecutionService().start_batch_run(uuid4(), [case.id], background_tasks=tasks)

    assert len(tasks.tasks) == 1
    tasks.tasks[0][0]()

    assert len(repo.results) == 1
    assert repo.results[0].status == ExecutionStatus.PASSED
    assert repo.results[0].test_cycle_id == batch_id
    assert repo.updated == [case]
    assert execution_service.active_batch_events[str(batch_id)][-1]["status"] == "Completed"
    assert execution_service.active_batch_events[str(batch_id)][-1]["passed_count"] == 1
