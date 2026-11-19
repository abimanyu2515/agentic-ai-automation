"""Permission grid for the data changing routes (spec 0009).

Each route gets the same checks, in this order: 401 without a login, 403
without the seeded permission, 404 for a missing project, 403 without project
access, 404 for a record from another project. A refused request starts no
work. Users come from the `sign_in` fixture (real tokens, seeded roles)."""

import logging
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import backend.dependencies.auth_dependencies as auth_module
import backend.main as main_module
from backend.main import app
from backend.repository.project_repository import JSONProjectRepository


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Two projects, A and B, each with one requirement, in an isolated JSON store.
    Scenario generation is replaced by a recorder, so no LLM is called."""
    import backend.repository.project_repository as repo_module

    previous = repo_module._active_project_repo
    repo = JSONProjectRepository(tmp_path / "store.json")
    repo_module._active_project_repo = repo

    def project_with_requirement(name):
        project = repo.create_project(name=name, description=name, target_url=f"https://{name.lower()}.example.com")
        requirement = repo.create_requirement(project.id, f"{name} login", "Users sign in", "medium", "general")
        return project.id, requirement.id

    a_project, a_requirement = project_with_requirement("Alpha")
    b_project, b_requirement = project_with_requirement("Beta")

    generated = []

    def fake_generate(project_id, requirement_id, count, mode="append"):
        generated.append((project_id, requirement_id))
        return []

    monkeypatch.setattr(main_module.workflow_service, "generate_scenarios", fake_generate)

    class World:
        pass

    state = World()
    state.repo = repo
    state.a_project, state.a_requirement = a_project, a_requirement
    state.b_project, state.b_requirement = b_project, b_requirement
    state.generated = generated
    yield state
    repo_module._active_project_repo = previous


def _generate_url(project_id, requirement_id):
    return f"/api/v1/projects/{project_id}/requirements/{requirement_id}/generate-scenarios"


def _generate(client, project_id, requirement_id, headers=None, query=""):
    return client.post(_generate_url(project_id, requirement_id) + query, json={"count": 2}, headers=headers or {})


# ---------------------------------------------------------------- generate scenarios


# covers: AC-2, AC-3
@pytest.mark.parametrize("role", ["Project Manager", "Tester"])
def test_assigned_role_with_scenarios_create_can_generate(client, world, sign_in, role):
    user = sign_in(role, projects=[world.a_project])

    response = _generate(client, world.a_project, world.a_requirement, user.headers)

    assert response.status_code == 200
    assert world.generated == [(world.a_project, world.a_requirement)]


# covers: AC-3
def test_admin_reaches_any_project_without_an_assignment(client, world, sign_in):
    admin = sign_in("Admin")

    response = _generate(client, world.a_project, world.a_requirement, admin.headers)

    assert response.status_code == 200


# covers: AC-1
def test_no_login_returns_401_and_starts_nothing(client, world, monkeypatch):
    monkeypatch.setattr(auth_module, "is_running_tests", lambda: False)

    response = _generate(client, world.a_project, world.a_requirement)

    assert response.status_code == 401
    assert world.generated == []


# covers: AC-1
def test_bad_token_returns_401(client, world, sign_in):
    sign_in("Tester")  # installs the RBAC database

    response = _generate(client, world.a_project, world.a_requirement, {"Authorization": "Bearer not-a-token"})

    assert response.status_code == 401
    assert world.generated == []


# covers: AC-2
def test_stakeholder_gets_403_with_the_chat_message(client, world, sign_in):
    stakeholder = sign_in("Stakeholder", projects=[world.a_project])

    response = _generate(client, world.a_project, world.a_requirement, stakeholder.headers)

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Access Denied: Role 'Stakeholder' does not have 'create' permission for 'Scenarios'"
    )
    assert world.generated == []


# covers: AC-2
def test_project_role_wins_over_global_role(client, world, sign_in):
    # A global Tester who is only a Stakeholder on this project is refused.
    user = sign_in("Tester", projects=[world.a_project], project_role="Stakeholder")

    response = _generate(client, world.a_project, world.a_requirement, user.headers)

    assert response.status_code == 403
    assert world.generated == []


# covers: AC-3
def test_tester_of_another_project_gets_403(client, world, sign_in):
    tester = sign_in("Tester", projects=[world.b_project])

    response = _generate(client, world.a_project, world.a_requirement, tester.headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to this project."
    assert world.generated == []


# covers: AC-3
def test_missing_project_returns_404(client, world, sign_in):
    manager = sign_in("Project Manager")

    response = _generate(client, uuid4(), world.a_requirement, manager.headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Project not found"


# covers: AC-2, AC-3 (order: permission is checked before the project lookup)
def test_permission_refusal_comes_before_the_project_lookup(client, world, sign_in):
    stakeholder = sign_in("Stakeholder")

    response = _generate(client, uuid4(), world.a_requirement, stakeholder.headers)

    assert response.status_code == 403


# covers: AC-4
def test_requirement_from_another_project_returns_404(client, world, sign_in):
    manager = sign_in("Project Manager", projects=[world.a_project])

    response = _generate(client, world.a_project, world.b_requirement, manager.headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Requirement not found"
    assert world.generated == []


# covers: AC-4
def test_deleted_requirement_returns_404(client, world, sign_in):
    manager = sign_in("Project Manager", projects=[world.a_project])
    world.repo.delete_requirement(world.a_requirement)

    response = _generate(client, world.a_project, world.a_requirement, manager.headers)

    assert response.status_code == 404
    assert world.generated == []


# ---------------------------------------------------------------- refusal log


def _refusals(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Request refused")]


# covers: AC-11
def test_permission_refusal_writes_one_warning_without_token_or_query(client, world, sign_in, caplog):
    stakeholder = sign_in("Stakeholder", projects=[world.a_project])
    token = stakeholder.headers["Authorization"].split()[1]

    with caplog.at_level(logging.WARNING, logger="backend.dependencies.auth_dependencies"):
        _generate(client, world.a_project, world.a_requirement, stakeholder.headers, query="?secret=hunter2")

    lines = _refusals(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert "reason=no_permission" in line
    assert f"user_id={stakeholder.id}" in line
    assert "role=Stakeholder" in line
    assert "method=POST" in line
    assert f"path={_generate_url(world.a_project, world.a_requirement)} " in line
    assert "module=Scenarios action=create" in line
    assert f"project_id={world.a_project}" in line
    assert "hunter2" not in line and "secret" not in line
    assert token not in line


# covers: AC-11
def test_project_access_refusal_logs_the_permission_it_was_checking(client, world, sign_in, caplog):
    tester = sign_in("Tester", projects=[world.b_project])

    with caplog.at_level(logging.WARNING, logger="backend.dependencies.auth_dependencies"):
        _generate(client, world.a_project, world.a_requirement, tester.headers)

    lines = _refusals(caplog)
    assert len(lines) == 1
    assert "reason=no_project_access" in lines[0]
    assert "module=Scenarios action=create" in lines[0]
    assert f"project_id={world.a_project}" in lines[0]


# ================================================================ the rest of the map
#
# `full` builds on `world`: each project also gets a scenario, a test case, a
# failed execution, and a release. Work the routes would start (execution)
# is replaced by recorders, so nothing reaches an LLM or browser.


@pytest.fixture
def full(world, monkeypatch):
    from backend.models.execution_result import ExecutionResult, ExecutionStatus
    from backend.models.scenario import Scenario
    from backend.models.test_case import TestCase
    from backend.services.execution_service import ExecutionService

    repo = world.repo
    for side in ("a", "b"):
        project_id = getattr(world, f"{side}_project")
        requirement_id = getattr(world, f"{side}_requirement")
        scenario = Scenario(
            requirement_id=requirement_id, scenario_name=f"{side} scenario", description="d", path_type="happy_path"
        )
        repo.save_scenarios([scenario])
        case = TestCase(scenario_id=scenario.id, title=f"{side} case", steps=["a"], expected_result="b")
        repo.save_test_cases([case])
        execution = ExecutionResult(test_case_id=case.id, status=ExecutionStatus.FAILED)
        repo.save_execution_result(project_id, execution)
        release = repo.create_release(project_id, f"{side} release")
        setattr(world, f"{side}_scenario", scenario.id)
        setattr(world, f"{side}_case", case.id)
        setattr(world, f"{side}_execution", execution.id)
        setattr(world, f"{side}_release", release.id)

    world.started = []

    def fake_single(self, project_id, test_case_id, background_tasks=None, workflow_service=None):
        world.started.append(("single", project_id, test_case_id))
        return uuid4()

    def fake_batch(self, project_id, test_case_ids, background_tasks=None, **kwargs):
        world.started.append(("batch", project_id, list(test_case_ids)))
        return uuid4()

    monkeypatch.setattr(ExecutionService, "start_test_case_run", fake_single)
    monkeypatch.setattr(ExecutionService, "start_batch_run", fake_batch)
    return world


REQUIREMENT_BODY = {"title": "t", "description": "d", "priority": "high", "business_domain": "g"}


def _project_routes(project, requirement, scenario, case, execution):
    """(method, url, body) for the project scoped user routes in the map."""
    base = f"/api/v1/projects/{project}"
    return [
        ("POST", f"{base}/requirements/{requirement}/generate-scenarios", {"count": 1}),
        ("PUT", f"{base}/scenarios/{scenario}", {"scenario_name": "x"}),
        ("DELETE", f"{base}/scenarios/{scenario}", None),
        ("POST", f"{base}/scenarios/duplicate/{scenario}", None),
        ("POST", f"{base}/requirements/{requirement}/generate-testcases", None),
        ("POST", f"{base}/testcases/{case}/generate-script", {"framework": "Playwright"}),
        ("POST", f"{base}/testcases/{case}/execute", None),
        ("POST", f"{base}/batches/execute", [str(case)]),
        ("POST", f"{base}/executions/{execution}/sync-bug", None),
        ("POST", f"{base}/releases", {"name": "R2"}),
        ("POST", f"{base}/requirements", REQUIREMENT_BODY),
    ]


# covers: AC-2, AC-3 (happy path)
def test_a_project_manager_on_the_project_can_do_the_whole_flow(client, full, sign_in):
    manager = sign_in("Project Manager", projects=[full.a_project]).headers
    base = f"/api/v1/projects/{full.a_project}"

    assert _generate(client, full.a_project, full.a_requirement, manager).status_code == 200
    renamed = client.put(f"{base}/scenarios/{full.a_scenario}", json={"scenario_name": "Renamed"}, headers=manager)
    assert renamed.status_code == 200
    duplicate = client.post(f"{base}/scenarios/duplicate/{full.a_scenario}", headers=manager)
    assert duplicate.status_code == 200 and duplicate.json()["id"] != str(full.a_scenario)
    assert client.post(f"{base}/testcases/{full.a_case}/execute", headers=manager).status_code == 200
    assert client.post(f"{base}/batches/execute", json=[str(full.a_case)], headers=manager).status_code == 200
    release = client.post(f"{base}/releases", json={"name": "R2"}, headers=manager)
    assert release.status_code == 201
    cycle = client.post(f"/api/v1/releases/{release.json()['id']}/cycles", json={"name": "C1"}, headers=manager)
    assert cycle.status_code == 201
    assert client.delete(f"{base}/scenarios/{full.a_scenario}", headers=manager).status_code == 200
    assert [kind for kind, *_ in full.started] == ["single", "batch"]


# covers: AC-1
def test_every_data_changing_route_needs_a_login_or_a_secret(client, monkeypatch):
    from fastapi.routing import APIRoute

    monkeypatch.setattr(auth_module, "is_running_tests", lambda: False)
    monkeypatch.setattr(main_module.get_settings().chat, "enabled", True)
    open_routes = {("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/refresh")}
    # Their body is validated first on purpose (AC-9); covered below.
    body_first = {("PUT", "/api/v1/playwright/freeze"), ("PUT", "/api/v1/playwright/script")}

    wrong = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            if (method, route.path) in open_routes | body_first:
                continue
            url = route.path
            for name in route.param_convertors:
                url = url.replace("{" + name + "}", str(uuid4()))
            status_code = client.request(method, url).status_code
            if status_code != 401:
                wrong.append((method, route.path, status_code))
    assert wrong == []


# covers: AC-2
@pytest.mark.parametrize(
    "role, method, target, message",
    [
        ("Stakeholder", "POST", "generate-scenarios", "'create' permission for 'Scenarios'"),
        ("Stakeholder", "POST", "execute", "'create' permission for 'Executions'"),
        ("Tester", "DELETE", "scenario", "'delete' permission for 'Scenarios'"),
        ("Tester", "POST", "requirements", "'create' permission for 'Requirements'"),
    ],
)
def test_roles_without_the_permission_get_403(client, full, sign_in, role, method, target, message):
    user = sign_in(role, projects=[full.a_project]).headers
    base = f"/api/v1/projects/{full.a_project}"
    url = {
        "generate-scenarios": f"{base}/requirements/{full.a_requirement}/generate-scenarios",
        "execute": f"{base}/testcases/{full.a_case}/execute",
        "scenario": f"{base}/scenarios/{full.a_scenario}",
        "requirements": f"{base}/requirements",
    }[target]
    body = REQUIREMENT_BODY if target == "requirements" else {"count": 1}

    response = client.request(method, url, json=body, headers=user)

    assert response.status_code == 403
    assert message in response.json()["detail"]
    assert full.generated == [] and full.started == []
    assert full.repo.get_scenario(full.a_scenario) is not None


# covers: AC-3
def test_a_tester_of_project_a_gets_403_on_every_project_b_route(client, full, sign_in):
    tester = sign_in("Tester", projects=[full.a_project]).headers

    routes = _project_routes(full.b_project, full.b_requirement, full.b_scenario, full.b_case, full.b_execution)
    statuses = {(m, url): client.request(m, url, json=body, headers=tester).status_code for m, url, body in routes}

    # Requirements.create and Scenarios.delete refuse on permission first;
    # every other route refuses on project access. All are 403.
    assert set(statuses.values()) == {403}, statuses
    assert full.generated == [] and full.started == []


# covers: AC-4
@pytest.mark.parametrize("record", ["requirement", "scenario", "case", "execution"])
def test_a_record_from_another_project_is_404_and_changes_nothing(client, full, sign_in, record):
    manager = sign_in("Project Manager", projects=[full.a_project]).headers
    base = f"/api/v1/projects/{full.a_project}"
    method, url, detail = {
        "requirement": ("POST", f"{base}/requirements/{full.b_requirement}/generate-testcases", "Requirement not found"),
        "scenario": ("DELETE", f"{base}/scenarios/{full.b_scenario}", "Scenario not found"),
        "case": ("POST", f"{base}/testcases/{full.b_case}/execute", "Test case not found"),
        "execution": ("POST", f"{base}/executions/{full.b_execution}/sync-bug", "Execution not found"),
    }[record]

    response = client.request(method, url, headers=manager)

    assert response.status_code == 404
    assert response.json()["detail"] == detail
    assert full.repo.get_scenario(full.b_scenario) is not None
    assert full.started == []


# covers: AC-4
def test_a_batch_with_one_foreign_test_case_queues_nothing(client, full, sign_in):
    manager = sign_in("Project Manager", projects=[full.a_project]).headers
    url = f"/api/v1/projects/{full.a_project}/batches/execute"

    response = client.post(url, json=[str(full.a_case), str(full.b_case)], headers=manager)

    assert response.status_code == 404
    assert full.started == []


# covers: AC-4
@pytest.mark.parametrize("body", [[], ["not-a-uuid"]])
def test_an_empty_or_malformed_batch_is_422(client, full, sign_in, body):
    manager = sign_in("Project Manager", projects=[full.a_project]).headers

    response = client.post(f"/api/v1/projects/{full.a_project}/batches/execute", json=body, headers=manager)

    assert response.status_code == 422
    assert full.started == []


# covers: AC-4
def test_duplicate_ids_in_a_batch_count_once(client, full, sign_in):
    manager = sign_in("Project Manager", projects=[full.a_project]).headers

    response = client.post(
        f"/api/v1/projects/{full.a_project}/batches/execute",
        json=[str(full.a_case), str(full.a_case)], headers=manager,
    )

    assert response.status_code == 200
    assert full.started == [("batch", full.a_project, [full.a_case])]


# covers: AC-4
def test_a_test_case_whose_scenario_was_deleted_is_404(client, full, sign_in):
    manager = sign_in("Project Manager", projects=[full.a_project]).headers
    full.repo.delete_scenario(full.a_scenario)

    response = client.post(f"/api/v1/projects/{full.a_project}/testcases/{full.a_case}/execute", headers=manager)

    assert response.status_code == 404
    assert full.started == []


# covers: AC-5
def test_routes_without_a_path_project_refuse_an_outsider(client, full, sign_in):
    outsider = sign_in("Project Manager", projects=[full.b_project]).headers
    script_body = {"project_id": str(full.a_project), "test_case_id": str(full.a_case), "script": "x"}

    responses = [
        client.post(f"/api/v1/scenarios/{full.a_scenario}/notes", json={"note": "n"}, headers=outsider),
        client.post(f"/api/v1/testcases/{full.a_case}/notes", json={"note": "n"}, headers=outsider),
        client.post(f"/api/v1/releases/{full.a_release}/cycles", json={"name": "C"}, headers=outsider),
        client.put("/api/v1/playwright/freeze", json={"test_case_id": str(full.a_case)}, headers=outsider),
        client.put("/api/v1/playwright/script", json=script_body, headers=outsider),
    ]

    assert [r.status_code for r in responses] == [403] * 5
    assert not full.repo.get_test_case(full.a_case).is_frozen


# covers: AC-5
def test_routes_without_a_path_project_return_404_for_a_missing_record(client, full, sign_in):
    manager = sign_in("Project Manager", projects=[full.a_project]).headers

    assert client.post(f"/api/v1/scenarios/{uuid4()}/notes", json={"note": "n"}, headers=manager).status_code == 404
    assert client.post(f"/api/v1/testcases/{uuid4()}/notes", json={"note": "n"}, headers=manager).status_code == 404
    assert client.post(f"/api/v1/releases/{uuid4()}/cycles", json={"name": "C"}, headers=manager).status_code == 404


# ---------------------------------------------------------------- secrets


@pytest.fixture
def vault(monkeypatch):
    from backend.services.vault_service import VaultService

    stored = {}
    monkeypatch.setattr(VaultService, "get_credentials", lambda self, pid: stored.get(pid, (None, None)))
    return stored


# covers: AC-6, AC-7 (a non ASCII value is a 401, never a 500)
@pytest.mark.parametrize("headers", [{}, {"X-Webhook-Secret": "wrong"}, {"X-Webhook-Secret": "pässwörd"}])
def test_workspace_routes_refuse_a_missing_wrong_or_non_ascii_secret(client, full, vault, headers):
    vault[str(full.a_project)] = ("alice", "pw-a")
    headers = {k: v.encode("utf-8") for k, v in headers.items()}  # raw bytes, as a client could send

    credentials = client.get(f"/api/v1/projects/{full.a_project}/credentials", headers=headers)
    webhook = client.post(
        f"/api/v1/projects/{full.a_project}/executions/webhook", json={"event": "started"}, headers=headers
    )

    assert credentials.status_code == 401
    assert webhook.status_code == 401
    assert "pw-a" not in credentials.text


# covers: AC-6
def test_credentials_return_only_the_projects_own_entry(client, full, vault, webhook_headers):
    vault[str(full.b_project)] = ("bob", "pw-b")

    own = client.get(f"/api/v1/projects/{full.b_project}/credentials", headers=webhook_headers())
    none = client.get(f"/api/v1/projects/{full.a_project}/credentials", headers=webhook_headers())

    assert own.status_code == 200 and own.json() == {"username": "bob", "password": "pw-b"}
    assert none.status_code == 404
    assert "bob" not in none.text


# covers: AC-6
def test_the_run_webhook_accepts_the_workspace_secret(client, full, webhook_headers):
    response = client.post(
        f"/api/v1/projects/{full.a_project}/executions/webhook",
        json={"event": "started", "test_case_id": str(full.a_case)},
        headers=webhook_headers(),
    )

    assert response.status_code == 200


def _jira_payload():
    return {"issue": {}}  # no issue key: the route answers "ignored" without calling Jira


# covers: AC-7
def test_the_jira_webhook_takes_the_secret_from_the_header_or_the_query(client, webhook_headers):
    url = "/api/v1/webhooks/jira"
    assert client.post(url, json=_jira_payload(), headers=webhook_headers("jira")).status_code == 200
    assert client.post(f"{url}?secret=test-jira-secret", json=_jira_payload()).status_code == 200
    assert client.post(f"{url}?secret=wrong", json=_jira_payload()).status_code == 401
    assert client.post(url, json=_jira_payload(), headers=webhook_headers()).status_code == 401


# covers: AC-7
def test_the_access_log_masks_the_secret_query():
    from backend.dependencies.auth_dependencies import SecretQueryFilter

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "POST", "/api/v1/webhooks/jira?a=1&secret=hunter2&b=2", "1.1", 200), None,
    )

    SecretQueryFilter().filter(record)

    message = record.getMessage()
    assert "hunter2" not in message
    assert "/api/v1/webhooks/jira?a=1&secret=***&b=2" in message


# covers: AC-7, AC-11
def test_a_bad_jira_secret_logs_bad_secret_without_the_value(client, caplog):
    with caplog.at_level(logging.WARNING, logger="backend.dependencies.auth_dependencies"):
        client.post("/api/v1/webhooks/jira?secret=hunter2", json=_jira_payload())

    lines = _refusals(caplog)
    assert len(lines) == 1
    assert "reason=bad_secret" in lines[0] and "user_id=machine" in lines[0] and "role=jira" in lines[0]
    assert "path=/api/v1/webhooks/jira " in lines[0]
    assert "hunter2" not in lines[0]


# covers: AC-8
@pytest.mark.parametrize(
    "field, var, path",
    [
        ("workspace_secret", "WORKSPACE_WEBHOOK_SECRET", "/api/v1/projects/{p}/executions/webhook"),
        ("jira_secret", "JIRA_WEBHOOK_SECRET", "/api/v1/webhooks/jira"),
    ],
)
def test_an_unset_secret_closes_its_routes_with_503(client, webhook_secrets, monkeypatch, caplog, field, var, path):
    from backend.dependencies.auth_dependencies import warn_missing_webhook_secrets

    monkeypatch.setattr(webhook_secrets, field, "")

    response = client.post(path.replace("{p}", str(uuid4())), json={"event": "x"})
    with caplog.at_level(logging.WARNING, logger="backend.dependencies.auth_dependencies"):
        warn_missing_webhook_secrets()

    assert response.status_code == 503
    assert response.json()["detail"] == f"Webhook secret not configured: {var}"
    warnings = [r.getMessage() for r in caplog.records if "Webhook secret not configured" in r.getMessage()]
    assert warnings == [f"Webhook secret not configured: {var}. Its routes return 503 until it is set."]


# ---------------------------------------------------------------- freeze and script (AC-9)


# covers: AC-9
def test_the_workspace_secret_can_freeze_and_save(client, full, webhook_headers):
    frozen = client.put(
        "/api/v1/playwright/freeze", json={"test_case_id": str(full.a_case), "is_frozen": False}, headers=webhook_headers()
    )
    saved = client.put(
        "/api/v1/playwright/script",
        json={"project_id": str(full.a_project), "test_case_id": str(full.a_case), "script": "// saved"},
        headers=webhook_headers(),
    )

    assert frozen.status_code == 200
    assert saved.status_code == 200  # regression: UnboundLocalError before spec 0009
    assert full.repo.get_test_case(full.a_case).playwright_script == "// saved"


# covers: AC-9
def test_an_editor_token_works_and_a_stakeholder_gets_403(client, full, sign_in):
    tester = sign_in("Tester", projects=[full.a_project]).headers
    stakeholder = sign_in("Stakeholder", projects=[full.a_project]).headers
    body = {"project_id": str(full.a_project), "test_case_id": str(full.a_case), "script": "// tester"}
    freeze = {"test_case_id": str(full.a_case)}

    assert client.put("/api/v1/playwright/script", json=body, headers=tester).status_code == 200
    assert client.put("/api/v1/playwright/script", json={**body, "script": "// no"}, headers=stakeholder).status_code == 403
    assert client.put("/api/v1/playwright/freeze", json=freeze, headers=stakeholder).status_code == 403
    assert client.put("/api/v1/playwright/freeze", json=freeze, headers=tester).status_code == 200
    assert full.repo.get_test_case(full.a_case).playwright_script == "// tester"


# covers: AC-9
def test_a_malformed_id_is_422_before_any_auth_check(client, full, monkeypatch):
    monkeypatch.setattr(auth_module, "is_running_tests", lambda: False)

    freeze = client.put("/api/v1/playwright/freeze", json={"test_case_id": "nope"}, headers={"X-Webhook-Secret": "wrong"})
    script = client.put(
        "/api/v1/playwright/script", json={"project_id": "nope", "test_case_id": str(full.a_case), "script": "x"}
    )

    assert freeze.status_code == 422
    assert script.status_code == 422


# covers: AC-9
def test_a_body_project_that_does_not_own_the_test_case_is_404(client, full, webhook_headers):
    response = client.put(
        "/api/v1/playwright/script",
        json={"project_id": str(full.b_project), "test_case_id": str(full.a_case), "script": "// hijack"},
        headers=webhook_headers(),
    )

    assert response.status_code == 404
    assert full.repo.get_test_case(full.a_case).playwright_script != "// hijack"


# ---------------------------------------------------------------- global routes (AC-13)


# covers: AC-13
def test_an_admin_without_settings_edit_cannot_change_tools_or_reset_demo_data(client, world, sign_in, monkeypatch):
    admin = sign_in("Admin").headers
    reset = []
    monkeypatch.setattr(world.repo, "reset_demo_data", lambda: reset.append(True))
    tool = {"tool_type": "jira", "tool_name": "x", "api_url": "https://x.example"}

    responses = [
        client.post("/api/v1/external-tools", json=tool, headers=admin),
        client.delete("/api/v1/external-tools/jira_x", headers=admin),
        client.post("/api/v1/admin/reset-demo-data", headers=admin),
    ]

    assert [r.status_code for r in responses] == [403] * 3
    assert all("'edit' permission for 'Settings'" in r.json()["detail"] for r in responses)
    assert reset == []


# ---------------------------------------------------------------- the UI's permission source (AC-10)
# Regression for /check verify 2026-11-19: the UI read permissions from
# /admin/permissions and /admin/roles, which only Admin and Superadmin may view,
# so every other role saw every gated button hidden and a 403 toast at sign in.


# covers: AC-10
@pytest.mark.parametrize(
    "role, allowed, denied",
    [
        ("Tester", {"Scenarios.create", "Scenarios.edit", "Executions.create"}, {"Scenarios.delete", "Requirements.create", "Permissions.view"}),
        ("Project Manager", {"Scenarios.delete", "Requirements.create", "Executions.create"}, {"Settings.edit", "Permissions.view"}),
        ("Stakeholder", {"Requirements.view"}, {"Scenarios.create", "Executions.create"}),
    ],
)
def test_auth_me_lists_the_users_own_permissions(client, sign_in, role, allowed, denied):
    user = sign_in(role)

    response = client.get("/api/v1/auth/me", headers=user.headers)

    assert response.status_code == 200
    permissions = set(response.json()["permissions"])
    assert allowed <= permissions
    assert not (denied & permissions)


# covers: AC-10
def test_auth_me_gives_superadmin_every_permission(client, sign_in):
    response = client.get("/api/v1/auth/me", headers=sign_in("Superadmin").headers)

    assert {"Settings.edit", "Permissions.view", "Scenarios.delete"} <= set(response.json()["permissions"])
