"""Spec 0012: batch runs carry their own batch_id, apart from test_cycle_id."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.repository.project_repository import JSONProjectRepository
from backend.services import execution_service
from backend.services.execution_service import ExecutionService


@pytest.fixture
def repo(tmp_path):
    import backend.repository.project_repository as repo_module

    previous = repo_module._active_project_repo
    repo_module._active_project_repo = JSONProjectRepository(tmp_path / "store.json")
    yield repo_module._active_project_repo
    repo_module._active_project_repo = previous


@pytest.fixture
def project(repo):
    return repo.create_project("Shop", "", framework="playwright", target_url="https://shop.example.test")


def _cycle(repo, project_id):
    release = repo.create_release(project_id, "R1")
    return repo.create_test_cycle(release.id, "C1")


def _run(**extra):
    return ExecutionResult(test_case_id=uuid4(), status=ExecutionStatus.PASSED, **extra)


def test_batch_read_is_project_scoped_and_ignores_cycles(repo, project):
    # AC-1, AC-6: runs are found by batch_id only, inside their project.
    other = repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    batch = uuid4()
    first, second = _run(batch_id=batch), _run(batch_id=batch)
    repo.save_execution_result(project.id, first)
    repo.save_execution_result(project.id, second)
    repo.save_execution_result(other.id, _run(batch_id=batch))
    repo.save_execution_result(project.id, _run(test_cycle_id=batch))

    found = repo.get_execution_results_for_batch(project.id, batch)

    assert {r.id for r in found} == {first.id, second.id}
    assert all(r.batch_id == batch for r in found)


def test_saved_batch_id_never_changes(repo, project):
    run = _run(batch_id=uuid4())
    repo.save_execution_result(project.id, run)
    repo.save_execution_result(project.id, run.model_copy(update={"batch_id": uuid4()}))

    assert repo.get_execution_result(project.id, run.id).batch_id == run.batch_id


def test_cycle_in_project(repo, project):
    # AC-2: a cycle counts only when its release belongs to this project.
    other = repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    cycle = _cycle(repo, project.id)
    service = ExecutionService()

    assert service.cycle_in_project(project.id, cycle.id)
    assert not service.cycle_in_project(other.id, cycle.id)
    assert not service.cycle_in_project(project.id, uuid4())


@pytest.fixture
def quiet_webhook(monkeypatch):
    monkeypatch.setattr(execution_service, "emit_execution_completed", lambda *args: None)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_markers", lambda *args: 0)
    monkeypatch.setattr(ExecutionService, "apply_accessibility", lambda *args, **kwargs: None)


def _webhook(project_id, **fields):
    payload = {"execution_id": str(uuid4()), "test_case_id": str(uuid4()), "status": "passed", **fields}
    return ExecutionService().finalize_execution(project_id, payload), payload


def test_webhook_saves_batch_id_and_checks_it(repo, project, quiet_webhook):
    # AC-3, AC-5
    batch = uuid4()
    saved, _ = _webhook(project.id, batch_id=str(batch))
    assert repo.get_execution_result(project.id, saved.id).batch_id == batch

    bad, _ = _webhook(project.id, batch_id="x")
    assert repo.get_execution_result(project.id, bad.id).batch_id is None


def test_repeated_webhook_keeps_first_batch_id(repo, project, quiet_webhook):
    # AC-5
    first, payload = _webhook(project.id, batch_id=str(uuid4()))
    ExecutionService().finalize_execution(project.id, {**payload, "batch_id": str(uuid4())})

    assert repo.get_execution_result(project.id, first.id).batch_id == first.batch_id


def test_webhook_drops_unknown_cycle_on_json_store(repo, project, quiet_webhook):
    # AC-5: a stray cycle is null on the JSON store too; a real one is kept.
    stray, _ = _webhook(project.id, test_cycle_id=str(uuid4()))
    assert repo.get_execution_result(project.id, stray.id).test_cycle_id is None

    cycle = _cycle(repo, project.id)
    real, _ = _webhook(project.id, test_cycle_id=str(cycle.id))
    assert repo.get_execution_result(project.id, real.id).test_cycle_id == cycle.id


def _log(project_id, *statuses):
    return [{"status": "Started", "project_id": str(project_id)}, *({"status": s} for s in statuses)]


def test_batch_log_state_and_in_progress(monkeypatch):
    # AC-7, AC-12, AC-13: the log answers only for the project that started it.
    project, other, batch = uuid4(), uuid4(), str(uuid4())
    events = {batch: _log(project, "Running")}
    monkeypatch.setattr(execution_service, "active_batch_events", events)

    assert ExecutionService.batch_log_state(project, batch) == "running"
    assert ExecutionService.batch_in_progress(project, batch) is True
    assert ExecutionService.batch_log_state(other, batch) is None
    assert ExecutionService.batch_in_progress(other, batch) is False

    events[batch].append({"status": "Completed"})
    assert ExecutionService.batch_log_state(project, batch) == "ended"
    assert ExecutionService.batch_in_progress(project, batch) is False
    events[batch][-1] = {"status": "Error"}
    assert ExecutionService.batch_log_state(project, batch) == "ended"

    assert ExecutionService.batch_log_state(project, uuid4()) is None
    # A log with no project tag (older shape) answers for nobody.
    events[batch] = [{"status": "Started"}]
    assert ExecutionService.batch_log_state(project, batch) is None


@pytest.fixture
def gate_api(repo, project):
    from backend.main import app, get_accessibility_service
    from backend.repository.accessibility_repository import InMemoryAccessibilityReportRepository
    from backend.services.accessibility_service import AccessibilityService

    service = AccessibilityService(repo=InMemoryAccessibilityReportRepository(), project_repo=repo)
    app.dependency_overrides[get_accessibility_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.pop(get_accessibility_service, None)


def test_gate_for_an_empty_running_batch(repo, project, gate_api, webhook_headers, monkeypatch):
    # AC-7, AC-12: no saved run yet, log still running, so 200 batch_running.
    other = repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    batch = uuid4()
    monkeypatch.setattr(execution_service, "active_batch_events", {str(batch): _log(project.id, "Running")})

    response = gate_api.get(
        f"/api/v1/projects/{project.id}/accessibility/gate", params={"batch_id": str(batch)}, headers=webhook_headers(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "gate_status": "NOT_SCANNED", "gate_reason": "batch_running", "gate_mode": None,
        "runs": [], "in_progress": True,
    }
    foreign = gate_api.get(
        f"/api/v1/projects/{other.id}/accessibility/gate", params={"batch_id": str(batch)}, headers=webhook_headers(),
    )
    assert foreign.status_code == 404


def test_gate_for_an_empty_ended_batch(repo, project, gate_api, webhook_headers, monkeypatch):
    # AC-13: the log ended before any run was saved, so 200 batch_no_runs;
    # once the log is gone (a restart) the same request is 404.
    batch = uuid4()
    events = {str(batch): _log(project.id, "Error")}
    monkeypatch.setattr(execution_service, "active_batch_events", events)
    url = f"/api/v1/projects/{project.id}/accessibility/gate"

    response = gate_api.get(url, params={"batch_id": str(batch)}, headers=webhook_headers())
    assert response.status_code == 200
    assert response.json() == {
        "gate_status": "NOT_SCANNED", "gate_reason": "batch_no_runs", "gate_mode": None,
        "runs": [], "in_progress": False,
    }
    events.clear()
    assert gate_api.get(url, params={"batch_id": str(batch)}, headers=webhook_headers()).status_code == 404


def test_gate_keeps_in_progress_once_runs_are_saved(repo, project, gate_api, webhook_headers, monkeypatch):
    # AC-12: with a saved run, the normal gate applies and in_progress follows the log.
    batch = uuid4()
    events = {str(batch): _log(project.id, "Running")}
    monkeypatch.setattr(execution_service, "active_batch_events", events)
    run = _run(batch_id=batch)
    repo.save_execution_result(project.id, run)
    url = f"/api/v1/projects/{project.id}/accessibility/gate"

    body = gate_api.get(url, params={"batch_id": str(batch)}, headers=webhook_headers()).json()
    assert body["gate_reason"] == "no_report"
    assert body["runs"] == [{"execution_id": str(run.id), "gate_status": "NOT_SCANNED"}]
    assert body["in_progress"] is True

    events[str(batch)].append({"status": "Completed"})
    assert gate_api.get(url, params={"batch_id": str(batch)}, headers=webhook_headers()).json()["in_progress"] is False


def test_json_backfill_moves_fake_cycle_ids_once(tmp_path):
    # AC-10
    repo = JSONProjectRepository(tmp_path / "store.json")
    project = repo.create_project("Shop", "", framework="playwright", target_url="https://shop.example.test")
    cycle = _cycle(repo, project.id)
    fake = uuid4()
    batch_run, cycle_run = _run(test_cycle_id=fake), _run(test_cycle_id=cycle.id)
    repo.save_execution_result(project.id, batch_run)
    repo.save_execution_result(project.id, cycle_run)

    assert repo.run_batch_identity_migration_0012() == 1
    moved = repo.get_execution_result(project.id, batch_run.id)
    assert (moved.batch_id, moved.test_cycle_id) == (fake, None)
    kept = repo.get_execution_result(project.id, cycle_run.id)
    assert (kept.batch_id, kept.test_cycle_id) == (None, cycle.id)

    before = json.loads((tmp_path / "store.json").read_text())
    assert repo.run_batch_identity_migration_0012() == 0
    assert json.loads((tmp_path / "store.json").read_text()) == before


def test_batch_start_route_refuses_another_projects_cycle(repo, project, sign_in, monkeypatch):
    # AC-2: a foreign cycle is a 404 and nothing is queued.
    from backend.main import app

    other = repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    cycle = _cycle(repo, other.id)
    monkeypatch.setattr(
        "backend.main.project_service.get_test_cases_for_project", lambda project_id: [],
    )
    started = []
    monkeypatch.setattr(ExecutionService, "start_batch_run", lambda *args, **kwargs: started.append(1))
    tester = sign_in("Tester", projects=[project.id])
    client = TestClient(app)

    response = client.post(
        f"/api/v1/projects/{project.id}/batches/execute",
        params={"test_cycle_id": str(cycle.id)},
        json=[],
        headers=tester.headers,
    )
    # An empty list is refused before the cycle check (spec 0009, AC-4).
    assert response.status_code == 422

    case_id = uuid4()
    monkeypatch.setattr(
        "backend.main.project_service.get_test_cases_for_project",
        lambda project_id: [type("Case", (), {"id": case_id})()],
    )
    response = client.post(
        f"/api/v1/projects/{project.id}/batches/execute",
        params={"test_cycle_id": str(cycle.id)},
        json=[str(case_id)],
        headers=tester.headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Test cycle not found"
    assert started == []


def test_batch_html_route_requires_a_session(repo, project, sign_in, monkeypatch):
    # AC-8: no login is a 401; another project's batch is a 404.
    import backend.dependencies.auth_dependencies as auth_module
    from backend.main import app

    batch = uuid4()
    repo.save_execution_result(project.id, _run(batch_id=batch))
    other = repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    viewer = sign_in("Tester", projects=[other.id])
    foreign = TestClient(app).get(f"/api/v1/projects/{other.id}/batches/{batch}/html", headers=viewer.headers)
    assert foreign.status_code == 404

    monkeypatch.setattr(auth_module, "is_running_tests", lambda: False)

    response = TestClient(app).get(f"/api/v1/projects/{project.id}/batches/{uuid4()}/html")
    assert response.status_code == 401


def test_batch_html_escapes_every_value(repo, project, monkeypatch):
    # AC-14: a hostile title, step text, and error show as text.
    from backend.models.test_case import TestCase
    from backend.services import report_service as report_module
    from backend.services.report_service import ReportService

    case = TestCase(scenario_id=uuid4(), title="<img src=x onerror=alert(1)>", steps=["s"], expected_result="e")
    repo.save_test_cases([case])
    batch = uuid4()
    run = ExecutionResult(
        test_case_id=case.id, status=ExecutionStatus.FAILED, batch_id=batch,
        error_message='<b onclick="x">boom</b>', timeline=[{"event": "click", "time": 1.0}],
    )
    repo.save_execution_result(project.id, run)
    monkeypatch.setattr(report_module, "map_timeline_event_to_step", lambda *args, **kwargs: {
        "title": "<script>alert(1)</script>", "action": "<i>a</i>", "observation": "<script>",
        "result": '"><svg onload=1>', "screenshot_url": "x", "screenshot_name": 'a"b<c>.png',
    })

    html_text = ReportService(repo=repo).compile_combined_batch_report_html(project.id, batch)

    assert "<img src=x" not in html_text and "&lt;img src=x onerror=alert(1)&gt;" in html_text
    assert "<script" not in html_text.lower()
    assert "<b onclick" not in html_text and "<svg" not in html_text and "<i>a</i>" not in html_text
    assert 'src="/api/artifacts/' in html_text and "a%22b%3Cc%3E.png" in html_text


def test_batch_html_route_hides_unexpected_errors(repo, project, sign_in, monkeypatch):
    # AC-8: only "no runs" is a 404; any other failure is a 500 with no exception text.
    from backend.main import app
    from backend.services.report_service import ReportService

    viewer = sign_in("Tester", projects=[project.id])
    client = TestClient(app)
    url = f"/api/v1/projects/{project.id}/batches/{uuid4()}/html"

    missing = client.get(url, headers=viewer.headers)
    assert missing.status_code == 404
    assert "No executions found" in missing.json()["detail"]

    def explode(*args):
        raise RuntimeError("secret path C:/store.json")

    monkeypatch.setattr(ReportService, "compile_combined_batch_report_html", explode)
    failed = client.get(url, headers=viewer.headers)
    assert failed.status_code == 500
    assert "secret" not in failed.text


def _stream_events(client, project_id, batch_id):
    response = client.get(f"/api/v1/projects/{project_id}/batches/{batch_id}/stream")
    assert response.status_code == 200
    return [json.loads(line[len("data: "):]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_answers_only_for_a_tracked_batch(monkeypatch):
    # AC-17: a tracked batch streams to Completed; an unknown id, an id that
    # isn't a UUID, and another project's batch each get one untracked Error.
    from backend.main import app

    project, other, batch = uuid4(), uuid4(), str(uuid4())
    monkeypatch.setitem(
        execution_service.active_batch_events, batch, _log(project, "Running", "Completed"),
    )
    client = TestClient(app)

    tracked = _stream_events(client, project, batch)
    assert [e["status"] for e in tracked] == ["Connecting", "Started", "Running", "Completed"]
    assert not any(e.get("untracked") for e in tracked)

    for project_id, batch_id in ((project, uuid4()), (project, "not-a-uuid"), (other, batch)):
        events = _stream_events(client, project_id, batch_id)
        assert events == [{
            "status": "Error",
            "untracked": True,
            "log": "This batch is not tracked on this server. It may belong to another project, or the server restarted.",
        }]


def test_execution_responses_carry_the_batch_id(repo, project, sign_in):
    # covers: AC-11. The list and the detail both return the run's batch_id,
    # and a run outside any batch returns null.
    from backend.main import app

    batch = uuid4()
    in_batch = _run(batch_id=batch)
    alone = _run()
    repo.save_execution_result(project.id, in_batch)
    repo.save_execution_result(project.id, alone)
    tester = sign_in("Tester", projects=[project.id])
    client = TestClient(app)

    listed = client.get(f"/api/v1/projects/{project.id}/executions", headers=tester.headers)
    assert listed.status_code == 200
    by_id = {row["id"]: row for row in listed.json()}
    assert by_id[str(in_batch.id)]["batch_id"] == str(batch)
    assert by_id[str(alone.id)]["batch_id"] is None

    detail = client.get(f"/api/v1/projects/{project.id}/executions/{in_batch.id}", headers=tester.headers)
    assert detail.status_code == 200
    assert detail.json()["execution"]["batch_id"] == str(batch)
    assert detail.json()["execution"]["test_cycle_id"] is None

    single = client.get(f"/api/v1/projects/{project.id}/executions/{alone.id}", headers=tester.headers)
    assert single.json()["execution"]["batch_id"] is None
