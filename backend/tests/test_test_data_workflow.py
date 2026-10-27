from types import SimpleNamespace
from uuid import uuid4

from backend.database.db_models import RequirementDB, TestCaseDB
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase
import backend.services.workflow_service as workflow_service_module


def test_test_data_columns_are_mapped_to_test_cases():
    requirement_columns = set(RequirementDB.__table__.columns.keys())
    test_case_columns = set(TestCaseDB.__table__.columns.keys())

    assert "test_data" not in requirement_columns
    assert "test_data_sets" not in requirement_columns
    assert {"test_data", "test_data_sets"} <= test_case_columns


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

    def get_scenarios_for_project(self, project_id):
        return list(self.saved_scenarios)

    def get_test_cases(self, scenario_id):
        return [
            test_case
            for test_case in self.saved_test_cases
            if test_case.scenario_id == scenario_id
        ]

    def get_test_case(self, test_case_id):
        return next(
            (case for case in self.saved_test_cases if case.id == test_case_id),
            None,
        )

    def get_scenario(self, scenario_id):
        return next(
            (scenario for scenario in self.saved_scenarios if scenario.id == scenario_id),
            None,
        )

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


def test_generate_unique_test_data_then_script_for_selected_case(monkeypatch):
    requirement = Requirement(
        title="Registration",
        description="Register a user with a valid email and password.",
        business_rules=["Password must contain a letter, a number, and a symbol."],
    )
    repository = FakeRepository(requirement)
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Reject numeric-only password",
        description="Reject a password that contains only numbers.",
    )
    selected_case = TestCase(
        scenario_id=scenario.id,
        title="Invalid Password: numeric-only input",
        steps=["Enter a password with only numeric characters", "Submit"],
        expected_result="Password validation error is shown",
        playwright_script="old script",
        test_data={"password": "00000000"},
    )
    other_scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Invalid email",
        description="Reject malformed email addresses.",
    )
    other_case = TestCase(
        scenario_id=other_scenario.id,
        title="Invalid email",
        steps=["Enter malformed email", "Submit"],
        expected_result="Email validation error is shown",
        test_data={"email": "not-an-email"},
    )
    repository.saved_scenarios = [scenario, other_scenario]
    repository.saved_test_cases = [selected_case, other_case]

    class StubTestDataAgent:
        def run(self, state):
            assert state.generated_test_cases == [selected_case]
            assert state.testing_context["reserved_test_data"] == [
                {"email": "not-an-email"}
            ]
            selected_datasets = [
                {
                    "password": f"{index:08d}",
                    "email": f"unique-user-{index}@example.test",
                }
                for index in range(20)
            ]
            state.generated_test_data = [
                {
                    "test_data_id": f"TD-{index + 1:02d}",
                    **dataset,
                    "targeted_validation_rule": "Password must contain a letter and symbol.",
                    "expected_result": selected_case.expected_result,
                    "test_type": "invalid",
                }
                for index, dataset in enumerate(selected_datasets)
            ]
            state.test_data_report = {
                "sample_dataset": {"rows": state.generated_test_data},
                "context_understood": {
                    "targeted_validation_rule": "Password must contain a letter and symbol."
                },
            }
            selected_case.test_data_sets = selected_datasets
            selected_case.test_data = selected_datasets[0]
            return state

    class StubPlaywrightAgent:
        def __init__(self, repo, max_workers, test_data_template):
            assert repo is repository
            assert max_workers == 1
            assert test_data_template is True

        def run(self, state):
            assert len(repository.get_test_case(selected_case.id).test_data_sets) == 20
            assert selected_case.playwright_script is None
            selected_case.playwright_script = "\n".join(
                f"test('data {index}', () => expect(true).toBe(true))" for index in range(20)
            )
            return state

    monkeypatch.setattr(
        workflow_service_module, "get_project_repository", lambda: repository
    )
    monkeypatch.setattr(workflow_service_module, "TestDataAgent", StubTestDataAgent)
    monkeypatch.setattr(workflow_service_module, "PlaywrightAgent", StubPlaywrightAgent)

    service = workflow_service_module.WorkflowService()
    report, result = service.generate_test_data_for_test_case(
        project_id=requirement.id,
        requirement_id=requirement.id,
        test_case_id=selected_case.id,
    )

    assert result.id == selected_case.id
    assert result.test_data["password"] == "00000000"
    assert len(result.test_data_sets) == 20
    assert result.playwright_script is None
    assert repository.get_test_case(selected_case.id).test_data == result.test_data
    assert len(repository.get_test_case(selected_case.id).test_data_sets) == 20
    assert repository.get_test_case(other_case.id).test_data == {"email": "not-an-email"}
    assert report["context_understood"]["targeted_validation_rule"].startswith("Password")

    def fail_if_project_data_lock_is_used(project_id):
        raise AssertionError(
            "script generation must not wait for project-wide dataset generation"
        )

    monkeypatch.setattr(
        workflow_service_module,
        "_test_data_generation_lock",
        fail_if_project_data_lock_is_used,
    )
    scripted_case = service.generate_test_data_script_for_test_case(
        project_id=requirement.id,
        requirement_id=requirement.id,
        test_case_id=selected_case.id,
    )

    assert scripted_case.id == selected_case.id
    assert scripted_case.playwright_script.count("test(") == 20


def test_generate_test_data_preserves_script_when_datasets_are_unchanged(monkeypatch):
    requirement = Requirement(
        title="Registration",
        description="Register a user with a valid email and password.",
    )
    repository = FakeRepository(requirement)
    scenario = Scenario(
        requirement_id=requirement.id,
        scenario_name="Reject numeric-only password",
        description="Reject a password that contains only numbers.",
    )
    datasets = [
        {"password": f"{index:08d}", "email": f"user-{index}@example.test"}
        for index in range(20)
    ]
    selected_case = TestCase(
        scenario_id=scenario.id,
        title="Invalid Password: numeric-only input",
        steps=["Enter a password with only numeric characters", "Submit"],
        expected_result="Password validation error is shown",
        test_data=datasets[0],
        test_data_sets=[dict(dataset) for dataset in datasets],
    )
    existing_script = "\n".join(
        f"test('TD-{selected_case.id.hex[:8].upper()}-{index:02d}', async () => {{}});"
        for index in range(1, 21)
    )
    selected_case.playwright_script = existing_script
    repository.saved_scenarios = [scenario]
    repository.saved_test_cases = [selected_case]

    class StubTestDataAgent:
        def run(self, state):
            state.generated_test_data = [
                {"test_data_id": f"TD-{index:02d}", **dataset}
                for index, dataset in enumerate(datasets, start=1)
            ]
            state.test_data_report = {"sample_dataset": {"rows": state.generated_test_data}}
            selected_case.test_data_sets = [dict(dataset) for dataset in datasets]
            selected_case.test_data = dict(datasets[0])
            return state

    monkeypatch.setattr(
        workflow_service_module, "get_project_repository", lambda: repository
    )
    monkeypatch.setattr(workflow_service_module, "TestDataAgent", StubTestDataAgent)

    _, result = workflow_service_module.WorkflowService().generate_test_data_for_test_case(
        project_id=requirement.id,
        requirement_id=requirement.id,
        test_case_id=selected_case.id,
    )

    assert result.playwright_script == existing_script
    assert result.test_data_sets == datasets


def test_generating_each_case_separately_persists_400_isolated_datasets(monkeypatch):
    requirement = Requirement(
        title="Swag Labs login",
        description="Validate the selected login test case only.",
    )
    repository = FakeRepository(requirement)
    scenarios = []
    cases = []
    for index in range(20):
        scenario = Scenario(
            requirement_id=requirement.id,
            scenario_name=f"Login scenario {index + 1}",
            description=f"Test login validation scenario {index + 1}.",
        )
        test_case = TestCase(
            scenario_id=scenario.id,
            title=f"Login test case {index + 1}",
            steps=["Enter login data", "Submit"],
            expected_result=f"Login result {index + 1}",
            test_data={"case_seed": str(index + 1)},
        )
        scenarios.append(scenario)
        cases.append(test_case)
    repository.saved_scenarios = scenarios
    repository.saved_test_cases = cases

    class StubTestDataAgent:
        def run(self, state):
            selected_case = state.generated_test_cases[0]
            datasets = [
                {
                    "test_case_id": str(selected_case.id),
                    "dataset_number": f"{index:02d}",
                }
                for index in range(1, 21)
            ]
            state.generated_test_cases[0].test_data_sets = datasets
            state.generated_test_cases[0].test_data = datasets[0]
            state.generated_test_data = [
                {"test_data_id": f"TD-{index:02d}", **dataset}
                for index, dataset in enumerate(datasets, start=1)
            ]
            state.test_data_report = {
                "sample_dataset": {"rows": state.generated_test_data}
            }
            return state

    monkeypatch.setattr(
        workflow_service_module, "get_project_repository", lambda: repository
    )
    monkeypatch.setattr(workflow_service_module, "TestDataAgent", StubTestDataAgent)
    service = workflow_service_module.WorkflowService()

    for selected_case in cases:
        other_case_snapshots = {
            case.id: (
                dict(case.test_data),
                [dict(dataset) for dataset in case.test_data_sets],
            )
            for case in cases
            if case.id != selected_case.id
        }
        report, generated_case = service.generate_test_data_for_test_case(
            project_id=requirement.id,
            requirement_id=requirement.id,
            test_case_id=selected_case.id,
        )

        assert generated_case.id == selected_case.id
        assert len(report["sample_dataset"]["rows"]) == 20
        assert len(generated_case.test_data_sets) == 20
        assert all(
            case.test_data == other_case_snapshots[case.id][0]
            and case.test_data_sets == other_case_snapshots[case.id][1]
            for case in cases
            if case.id != selected_case.id
        )

    all_datasets = [
        dataset
        for test_case in cases
        for dataset in test_case.test_data_sets
    ]
    assert len(all_datasets) == 400
    assert len({
        tuple(sorted(dataset.items()))
        for dataset in all_datasets
    }) == 400
