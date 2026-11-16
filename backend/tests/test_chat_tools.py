"""Chat read tools (spec 0005, slice 2): project scoping, redaction, and the
result cap. Uses an in memory fake repository; no LLM or database."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.object_repository import ObjectElement, ObjectPage, ProjectObjectRepository
from backend.models.requirement import Requirement
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase
from backend.services.chat_tools import NOT_FOUND, READ_TOOL_NAMES, ChatToolbox, fit_result
from backend.services.llm import LLMService


class FakeRepo:
    def __init__(self):
        self.requirements: dict = {}
        self.requirement_project: dict = {}
        self.scenarios: dict = {}
        self.test_cases: dict = {}
        self.executions: dict = {}  # project_id -> list

    def get_requirements(self, project_id):
        return [r for r in self.requirements.values() if self.requirement_project[r.id] == project_id]

    def get_requirement(self, requirement_id):
        return self.requirements.get(requirement_id)

    def get_scenarios(self, requirement_id):
        return [s for s in self.scenarios.values() if s.requirement_id == requirement_id]

    def get_scenario(self, scenario_id):
        return self.scenarios.get(scenario_id)

    def get_scenarios_for_project(self, project_id):
        ids = {r.id for r in self.get_requirements(project_id)}
        return [s for s in self.scenarios.values() if s.requirement_id in ids]

    def get_test_cases(self, scenario_id):
        return [t for t in self.test_cases.values() if t.scenario_id == scenario_id]

    def get_test_case(self, test_case_id):
        return self.test_cases.get(test_case_id)

    def get_test_cases_for_project(self, project_id):
        ids = {s.id for s in self.get_scenarios_for_project(project_id)}
        return [t for t in self.test_cases.values() if t.scenario_id in ids]

    def get_execution_results(self, project_id):
        return list(self.executions.get(project_id, []))

    def get_execution_result(self, project_id, execution_id):
        return next((e for e in self.executions.get(project_id, []) if e.id == execution_id), None)

    def get_test_cycle(self, cycle_id):
        return None


class FakeObjectStore:
    def __init__(self, repos):
        self.repos = repos

    def load(self, project_id):
        return self.repos.get(project_id, ProjectObjectRepository(project_id=project_id))


def _requirement(repo, project, title):
    requirement = Requirement(title=title, description=f"{title} description")
    repo.requirement_project[requirement.id] = project.id
    repo.requirements[requirement.id] = requirement
    project.requirements.append(requirement.id)
    return requirement


@pytest.fixture
def world():
    repo = FakeRepo()
    project = SimpleNamespace(
        id=uuid4(), name="Shop", description="d", target_url="https://shop.test", framework="playwright",
        line_of_business="retail", jira_project_key=None, target_username="alice", target_password_enc="enc",
        requirements=[],
    )
    other = SimpleNamespace(id=uuid4(), name="Other", requirements=[])
    login = _requirement(repo, project, "Login")
    _requirement(repo, project, "Checkout")
    foreign_req = _requirement(repo, other, "Foreign")

    scenario = Scenario(requirement_id=login.id, scenario_name="Valid login", description="d", approved=True)
    foreign_scenario = Scenario(requirement_id=foreign_req.id, scenario_name="X", description="d")
    repo.scenarios = {scenario.id: scenario, foreign_scenario.id: foreign_scenario}

    test_case = TestCase(
        scenario_id=scenario.id, title="Login works", steps=["open", "submit"], expected_result="home",
        test_data={"username": "bob", "Password": "hunter2", "api_key": "k"},
        test_data_sets=[{"user": "x", "auth_token": "t"}],
        playwright_script="x" * 5000,
    )
    foreign_tc = TestCase(scenario_id=foreign_scenario.id, title="Foreign", steps=["a"], expected_result="b")
    repo.test_cases = {test_case.id: test_case, foreign_tc.id: foreign_tc}

    now = datetime.now(timezone.utc)
    older = ExecutionResult(test_case_id=test_case.id, status=ExecutionStatus.PASSED, executed_at=now - timedelta(hours=2))
    newer = ExecutionResult(
        test_case_id=test_case.id, status=ExecutionStatus.FAILED, executed_at=now, error_message="Timeout",
        timeline=[{"event": "open page", "type": "info"}, {"event": "click Submit", "type": "error"}],
        root_cause_summary="Button moved", trace_path="t.zip",
    )
    foreign_exec = ExecutionResult(test_case_id=foreign_tc.id, status=ExecutionStatus.FAILED)
    repo.executions = {project.id: [older, newer], other.id: [foreign_exec]}

    page = ObjectPage(project_id=project.id, name="LoginPage", url_pattern="/login")
    page.elements = [ObjectElement(page_id=page.id, name="submit", element_type="button")]
    store = FakeObjectStore({project.id: ProjectObjectRepository(project_id=project.id, pages=[page])})

    toolbox = ChatToolbox(repo, store, result_chars=4000)

    def run(name, **arguments):
        return json.loads(toolbox.run(project, name, arguments))

    return SimpleNamespace(
        repo=repo, project=project, toolbox=toolbox, run=run, login=login, scenario=scenario,
        test_case=test_case, newer=newer, foreign_req=foreign_req, foreign_scenario=foreign_scenario,
        foreign_tc=foreign_tc, foreign_exec=foreign_exec,
    )


def test_every_tool_has_a_definition_and_runs(world):
    assert {t["function"]["name"] for t in world.toolbox.definitions()} == READ_TOOL_NAMES
    for name in READ_TOOL_NAMES:
        result = world.run(name)
        assert "error" not in result or result["error"].endswith("is required"), (name, result)


def test_overview_has_counts_and_no_credentials(world):
    result = world.run("get_project_overview")
    assert result["counts"] == {"requirements": 2, "scenarios": 1, "test_cases": 1, "executions": 2}
    text = json.dumps(result)
    assert "alice" not in text and "enc" not in text


def test_list_requirements_returns_priority_and_counts_with_query(world):
    result = world.run("list_requirements", query="log")
    assert result["total"] == 1
    row = result["items"][0]
    assert (row["title"], row["priority"], row["scenarios"], row["test_cases"]) == ("Login", "medium", 1, 1)


def test_list_test_cases_shows_script_and_latest_execution(world):
    row = world.run("list_test_cases", requirement_id=str(world.login.id))["items"][0]
    assert row["has_script"] is True
    assert row["last_execution_status"] == "failed"


def test_get_test_case_redacts_secrets_and_cuts_script(world):
    result = world.run("get_test_case", test_case_id=str(world.test_case.id))
    assert result["test_data"] == {"username": "bob", "Password": "[redacted]", "api_key": "[redacted]"}
    assert result["test_data_sets"] == [{"user": "x", "auth_token": "[redacted]"}]
    assert len(result["script"]) == 2000 + len("…[truncated]")


def test_get_execution_reports_failed_step_and_artifacts(world):
    result = world.run("get_execution", execution_id=str(world.newer.id))
    assert result["failed_step"] == "click Submit"
    assert result["analysis"]["root_cause_summary"] == "Button moved"
    assert result["artifacts"] == {"screenshot": False, "video": False, "trace": True}


def test_list_executions_newest_first_with_filter(world):
    result = world.run("list_executions")
    assert [r["status"] for r in result["items"]] == ["failed", "passed"]
    assert world.run("list_executions", status="passed")["total"] == 1


@pytest.mark.parametrize("name, field, attr", [
    ("get_requirement", "requirement_id", "foreign_req"),
    ("list_scenarios", "requirement_id", "foreign_req"),
    ("list_test_cases", "scenario_id", "foreign_scenario"),
    ("list_test_cases", "requirement_id", "foreign_req"),
    ("get_test_case", "test_case_id", "foreign_tc"),
    ("list_executions", "test_case_id", "foreign_tc"),
    ("get_execution", "execution_id", "foreign_exec"),
])
def test_ids_from_another_project_are_not_found(world, name, field, attr):
    assert world.run(name, **{field: str(getattr(world, attr).id)}) == {"error": NOT_FOUND}


def test_bad_ids_unknown_tools_and_bad_json_return_errors(world):
    assert world.run("get_test_case", test_case_id="nope") == {"error": NOT_FOUND}
    assert world.run("get_test_case") == {"error": "test_case_id is required"}
    assert json.loads(world.toolbox.run(world.project, "drop_tables", {}))["error"].startswith("unknown tool")
    assert "error" in json.loads(world.toolbox.run(world.project, "list_requirements", None))


def test_limit_defaults_to_20_and_caps_at_50(world):
    for index in range(60):
        _requirement(world.repo, world.project, f"R{index}")
    world.toolbox.result_chars = 100_000
    assert len(world.run("list_requirements")["items"]) == 20
    assert len(world.run("list_requirements", limit=500)["items"]) == 50
    assert world.run("list_requirements", limit=500)["total"] == 62


def test_object_repository_pages_and_dashboard(world):
    assert world.run("list_object_repository_pages")["items"][0]["elements"] == 1
    assert world.run("get_dashboard_metrics")["totalTestCases"] == 1


def test_fit_result_drops_rows_from_the_end_and_keeps_valid_json():
    payload = {"items": [{"id": i, "text": "x" * 100} for i in range(50)], "total": 50}
    text = fit_result(payload, 1000)
    data = json.loads(text)
    assert len(text) <= 1000
    assert data["truncated"] is True and data["total"] == 50
    assert [row["id"] for row in data["items"]] == list(range(len(data["items"])))


def test_fit_result_cuts_the_longest_text_of_a_single_record():
    text = fit_result({"steps": ["short"], "script": "y" * 9000, "note": "z" * 3000}, 4000)
    data = json.loads(text)
    assert len(text) <= 4000
    assert data["script"].endswith("…[truncated]")
    assert data["steps"] == ["short"]


# ---------------------------------------------------- tool call reassembly


def _delta_chunk(content=None, tool_calls=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _piece(index, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


def test_stream_chunks_puts_tool_calls_back_together():
    config = SimpleNamespace(provider="openai", model="m")
    stream = iter([
        _delta_chunk(tool_calls=[_piece(0, id="c1", name="get_test_case", arguments='{"test_')]),
        _delta_chunk(tool_calls=[_piece(0, arguments='case_id": "abc"}'), _piece(1, id="c2", name="list_requirements")]),
        _delta_chunk(tool_calls=[_piece(1, arguments="{not json")]),
    ])
    chunks = list(LLMService._stream_chunks(stream, _delta_chunk(content="Let me check. "), config))
    assert chunks[0].text == "Let me check. "
    first, second = chunks[1].tool_call, chunks[2].tool_call
    assert (first.id, first.name, first.arguments) == ("c1", "get_test_case", {"test_case_id": "abc"})
    assert (second.id, second.name, second.arguments, second.raw_arguments) == ("c2", "list_requirements", None, "{not json")


def test_dashboard_route_and_chat_tool_return_the_same_numbers(world, monkeypatch):
    from fastapi.testclient import TestClient

    import backend.main as main

    service = SimpleNamespace(repo=world.repo, get_project=lambda pid: world.project if pid == world.project.id else None)
    monkeypatch.setattr(main, "project_service", service)
    http = TestClient(main.app)
    response = http.get(f"/api/v1/projects/{world.project.id}/dashboard-metrics")
    assert response.status_code == 200
    body = response.json()
    assert body == json.loads(json.dumps(world.run("get_dashboard_metrics"), default=str)) | {
        "recentExecutions": body["recentExecutions"],
    }
    assert [e["status"] for e in body["recentExecutions"]] == ["failed", "passed"]
    assert (body["totalReqs"], body["passRatio"], body["failedCount"]) == (2, "50%", 1)
    assert http.get(f"/api/v1/projects/{uuid4()}/dashboard-metrics").status_code == 404
