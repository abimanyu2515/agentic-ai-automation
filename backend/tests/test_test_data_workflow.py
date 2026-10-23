from types import SimpleNamespace
from uuid import uuid4

from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase
import backend.services.workflow_service as workflow_service_module


class FakeRepository:
    def __init__(self, requirement):
        self.project = SimpleNamespace(framework="playwright")
        self.requirement = requirement
        self.saved_scenarios = []
        self.saved_test_cases = []

    def get_project(self, project_id):
        return self.project

    def get_requirements(self, project_id):
        return [self.requirement]

    def get_scenarios(self, requirement_id):
        return [
            scenario
            for scenario in self.saved_scenarios
            if scenario.requirement_id == requirement_id
        ]

    def get_test_cases(self, scenario_id):
        return [
            test_case
            for test_case in self.saved_test_cases
            if test_case.scenario_id == scenario_id
        ]

    def save_scenarios(self, scenarios):
        self.saved_scenarios.extend(scenarios)

    def save_test_cases(self, test_cases):
        saved_indexes = {
            test_case.id: index
            for index, test_case in enumerate(self.saved_test_cases)
        }
        for test_case in test_cases:
            index = saved_indexes.get(test_case.id)
            if index is None:
                saved_indexes[test_case.id] = len(self.saved_test_cases)
                self.saved_test_cases.append(test_case)
            else:
                self.saved_test_cases[index] = test_case


def test_generate_test_data_test_cases_creates_20_scripted_cases(monkeypatch):
    requirement = Requirement(title="Registration", description="Register a user.")
    repository = FakeRepository(requirement)
    rows = [
        {
            "test_data_id": f"TD-{index:03d}",
            "email": f"user{index}@example.com",
            "expected_result": "accepted",
            "test_type": "positive",
        }
        for index in range(1, 21)
    ]
    report = {"sample_dataset": {"rows": rows}}

    class StubTestDataAgent:
        def run(self, state):
            state.generated_test_data = rows
            state.test_data_report = report
            return state

    class StubPlaywrightAgent:
        def __init__(self, repo, max_workers, test_data_template):
            self.repo = repo
            assert max_workers == 5
            assert test_data_template is True

        def run(self, state):
            assert len(self.repo.saved_test_cases) == 20
            for case in state.generated_test_cases:
                case.playwright_script = "import { test, expect } from '@playwright/test';"
            return state

    monkeypatch.setattr(
        workflow_service_module, "get_project_repository", lambda: repository
    )
    monkeypatch.setattr(workflow_service_module, "TestDataAgent", StubTestDataAgent)
    monkeypatch.setattr(workflow_service_module, "PlaywrightAgent", StubPlaywrightAgent)

    result_report, cases = workflow_service_module.WorkflowService().generate_test_data_test_cases(
        requirement_id=requirement.id,
        project_id=requirement.id,
    )

    assert result_report == report
    assert len(cases) == 20
    assert len(repository.saved_scenarios) == 20
    assert len(repository.saved_test_cases) == 20
    assert all(case.playwright_script for case in cases)
    assert all(case.evaluation_status.value == "approved" for case in cases)
    assert cases[0].title == "Test data TD-001: positive"
    assert cases[0].steps[0].endswith("Expected result: accepted.")
    assert cases[0].test_data == {"email": "user1@example.com"}
    assert cases[0].expected_result == "accepted"


def test_generate_test_data_cases_skips_script_generation(monkeypatch):
    requirement = Requirement(title="Registration", description="Register a user.")
    repository = FakeRepository(requirement)
    rows = [
        {
            "test_data_id": f"TD-{index:03d}",
            "email": f"user{index}@example.com",
            "expected_result": "accepted",
            "test_type": "positive",
        }
        for index in range(1, 21)
    ]

    class StubTestDataAgent:
        def run(self, state):
            state.generated_test_data = rows
            state.test_data_report = {"sample_dataset": {"rows": rows}}
            return state

    class UnexpectedPlaywrightAgent:
        def __init__(self, repo):
            raise AssertionError("script generation should not run for TestDataAgent")

    monkeypatch.setattr(
        workflow_service_module, "get_project_repository", lambda: repository
    )
    monkeypatch.setattr(workflow_service_module, "TestDataAgent", StubTestDataAgent)
    monkeypatch.setattr(
        workflow_service_module, "PlaywrightAgent", UnexpectedPlaywrightAgent
    )

    report, cases = workflow_service_module.WorkflowService().generate_test_data_test_cases(
        project_id=requirement.id,
        requirement_id=requirement.id,
        generate_scripts=False,
    )

    assert len(report["sample_dataset"]["rows"]) == 20
    assert len(cases) == 20
    assert len(repository.saved_scenarios) == 20
    assert len(repository.saved_test_cases) == 20
    assert all(case.playwright_script is None for case in cases)


def test_repeated_run_reuses_cases_and_generates_only_missing_scripts(monkeypatch):
    requirement = Requirement(title="Registration", description="Register a user.")
    repository = FakeRepository(requirement)
    rows = [
        {
            "test_data_id": f"TD-{index:03d}",
            "email": f"user{index}@example.com",
            "expected_result": "accepted",
            "test_type": "positive",
        }
        for index in range(1, 21)
    ]
    script_batches = []

    class StubTestDataAgent:
        def run(self, state):
            state.generated_test_data = rows
            state.test_data_report = {"sample_dataset": {"rows": rows}}
            return state

    class StubPlaywrightAgent:
        def __init__(self, repo, max_workers, test_data_template):
            assert max_workers == 5
            assert test_data_template is True

        def run(self, state):
            script_batches.append([case.id for case in state.generated_test_cases])
            for case in state.generated_test_cases:
                case.playwright_script = "import { test, expect } from '@playwright/test';"
            return state

    monkeypatch.setattr(
        workflow_service_module, "get_project_repository", lambda: repository
    )
    monkeypatch.setattr(workflow_service_module, "TestDataAgent", StubTestDataAgent)
    monkeypatch.setattr(workflow_service_module, "PlaywrightAgent", StubPlaywrightAgent)
    service = workflow_service_module.WorkflowService()

    _, initial_cases = service.generate_test_data_test_cases(
        project_id=requirement.id,
        requirement_id=requirement.id,
        generate_scripts=False,
    )
    for case in initial_cases[:8]:
        case.playwright_script = "already generated"
    repository.save_test_cases(initial_cases)
    for scenario, case in zip(repository.saved_scenarios[:8], initial_cases[:8]):
        duplicate_scenario = scenario.model_copy(update={"id": uuid4()})
        duplicate_case = case.model_copy(
            update={
                "id": uuid4(),
                "scenario_id": duplicate_scenario.id,
                "playwright_script": None,
            }
        )
        repository.save_scenarios([duplicate_scenario])
        repository.save_test_cases([duplicate_case])

    _, scripted_cases = service.generate_test_data_test_cases(
        project_id=requirement.id,
        requirement_id=requirement.id,
    )
    _, repeated_cases = service.generate_test_data_test_cases(
        project_id=requirement.id,
        requirement_id=requirement.id,
    )

    assert len(repository.saved_scenarios) == 28
    assert len(repository.saved_test_cases) == 28
    assert [case.id for case in scripted_cases] == [case.id for case in initial_cases]
    assert [case.id for case in repeated_cases] == [case.id for case in initial_cases]
    assert len(script_batches) == 1
    assert len(script_batches[0]) == 12
    assert all(case.playwright_script for case in repeated_cases)
