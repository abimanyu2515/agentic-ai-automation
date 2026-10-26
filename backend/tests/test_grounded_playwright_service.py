from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.models.project import Project
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.test_case import EvaluationStatus, ScriptValidationStatus, TestCase
from backend.services import grounded_playwright_service as service_module
from backend.services.grounded_playwright_service import GroundedPlaywrightScriptService
from backend.models.state import WorkflowState


class MemoryRepository:
    def __init__(self, test_cases):
        self.requirement = Requirement(title="Login", description="Login flow")
        self.scenario = Scenario(
            requirement_id=self.requirement.id,
            scenario_name="Invalid login",
            description="Reject invalid credentials",
        )
        self.project = Project(
            name="Test project",
            framework="playwright",
            target_url="https://example.test",
            is_safe_test_environment=True,
            requirements=[self.requirement.id],
        )
        self.test_cases = {test_case.id: test_case for test_case in test_cases}
        for test_case in self.test_cases.values():
            test_case.scenario_id = self.scenario.id

    def get_project(self, project_id):
        return self.project if project_id == self.project.id else None

    def get_test_case(self, test_case_id):
        return self.test_cases.get(test_case_id)

    def get_scenario(self, scenario_id):
        return self.scenario if scenario_id == self.scenario.id else None

    def get_requirement(self, requirement_id):
        return self.requirement if requirement_id == self.requirement.id else None

    def update_test_case(self, test_case):
        self.test_cases[test_case.id] = test_case
        return test_case


class FakeInspector:
    def __init__(self):
        self.calls = []

    def inspect_project(self, project_id, test_cases, allow_invalid_login_probe):
        self.calls.append((project_id, [case.id for case in test_cases], allow_invalid_login_probe))
        return {"pages": [{"title": "Login"}]}


class ScriptAgent:
    def __init__(self, fail_for=None):
        self.calls = []
        self.fail_for = fail_for

    def run(self, state: WorkflowState):
        case = state.generated_test_cases[0]
        self.calls.append((case.id, state.repair_feedback, state.dom_context))
        if case.id == self.fail_for:
            raise RuntimeError("generation failed")
        case.playwright_script = f"script attempt {len(self.calls)}"
        return state


class FakeRunner:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.calls = []

    def run(self, script, run_id, project_id=None):
        self.calls.append((script, project_id))
        return SimpleNamespace(status=next(self.statuses))


def _test_case(title="Invalid login"):
    return TestCase(
        scenario_id=uuid4(),
        title=title,
        steps=["Enter invalid credentials", "Submit the form"],
        expected_result="The invalid login error is displayed",
        evaluation_status=EvaluationStatus.APPROVED,
        test_data={"username": "invalid-user", "password": "invalid-password"},
    )


def test_batch_repairs_once_and_persists_validated_status(monkeypatch):
    test_case = _test_case()
    repo = MemoryRepository([test_case])
    inspector = FakeInspector()
    agent = ScriptAgent()
    runner = FakeRunner(["failed", "passed"])
    monkeypatch.setattr(service_module, "validate_playwright_script", lambda *args: [])
    service = GroundedPlaywrightScriptService(
        repo=repo,
        runner=runner,
        inspector=inspector,
        agent_factory=lambda: agent,
    )

    reserved = service.reserve_batch(repo.project.id, [test_case.id])
    assert reserved[0].script_validation_status == ScriptValidationStatus.RUNNING

    service.run_reserved_batch(repo.project.id, [test_case.id])

    persisted = repo.get_test_case(test_case.id)
    assert persisted.script_validation_status == ScriptValidationStatus.VALIDATED
    assert persisted.script_validation_attempts == 2
    assert len(agent.calls) == 2
    assert agent.calls[1][1] is not None
    assert len(inspector.calls) == 2
    assert len(runner.calls) == 2


def test_batch_continues_after_one_case_generation_fails(monkeypatch):
    failed_case, passing_case = _test_case("Fails"), _test_case("Passes")
    repo = MemoryRepository([failed_case, passing_case])
    inspector = FakeInspector()
    agent = ScriptAgent(fail_for=failed_case.id)
    runner = FakeRunner(["passed"])
    monkeypatch.setattr(service_module, "validate_playwright_script", lambda *args: [])
    service = GroundedPlaywrightScriptService(
        repo=repo,
        runner=runner,
        inspector=inspector,
        agent_factory=lambda: agent,
    )

    service.reserve_batch(repo.project.id, [failed_case.id, passing_case.id])
    service.run_reserved_batch(repo.project.id, [failed_case.id, passing_case.id])

    assert repo.get_test_case(failed_case.id).script_validation_status == ScriptValidationStatus.FAILED
    assert repo.get_test_case(passing_case.id).script_validation_status == ScriptValidationStatus.VALIDATED
    assert len(runner.calls) == 1


def test_batch_refuses_unmarked_test_environment():
    test_case = _test_case()
    repo = MemoryRepository([test_case])
    repo.project.is_safe_test_environment = False
    service = GroundedPlaywrightScriptService(repo=repo)

    with pytest.raises(ValueError, match="safe test environment"):
        service.reserve_batch(repo.project.id, [test_case.id])
