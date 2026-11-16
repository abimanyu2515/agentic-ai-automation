"""Background script generation jobs: the service and its API routes."""

import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.dependencies.auth_dependencies import get_current_user
from backend.models.test_case import TestCase
from backend.services.script_generation_jobs import (
    COMPLETED,
    FAILED,
    RUNNING,
    ScriptGenerationJobs,
)


def _case() -> TestCase:
    return TestCase(
        scenario_id=uuid4(),
        title="Valid user logs in",
        steps=["Open the login page"],
        expected_result="The Search Hotel page is displayed",
        playwright_script="import { test } from '@playwright/test';",
    )


class _Generator:
    """Blocks until released, then returns a test case or raises."""

    def __init__(self, result=None):
        self.release = threading.Event()
        self.result = result if result is not None else _case()
        self.calls = []

    def __call__(self, project_id, test_case_id, framework):
        self.calls.append((project_id, test_case_id, framework))
        self.release.wait(5)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _wait_until_finished(jobs, project_id, job_id):
    for _ in range(200):
        job = jobs.get(project_id, job_id)
        if job.status != RUNNING:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_job_runs_in_background_and_completes():
    generator = _Generator()
    jobs = ScriptGenerationJobs(generator)
    project_id, test_case_id = uuid4(), uuid4()

    job = jobs.start(project_id, test_case_id, "Playwright")

    # start returns while generation is still running.
    assert job.status == RUNNING
    generator.release.set()
    finished = _wait_until_finished(jobs, project_id, job.id)
    assert finished.status == COMPLETED
    assert finished.test_case is generator.result
    assert generator.calls == [(project_id, test_case_id, "Playwright")]


def test_second_start_for_same_test_case_reuses_running_job():
    generator = _Generator()
    jobs = ScriptGenerationJobs(generator)
    project_id, test_case_id = uuid4(), uuid4()

    first = jobs.start(project_id, test_case_id, "Playwright")
    second = jobs.start(project_id, test_case_id, "Playwright")
    other = jobs.start(project_id, uuid4(), "Playwright")

    assert second.id == first.id
    assert other.id != first.id
    generator.release.set()
    _wait_until_finished(jobs, project_id, first.id)
    _wait_until_finished(jobs, project_id, other.id)
    assert len(generator.calls) == 2


@pytest.mark.parametrize(
    "error, message, error_status",
    [
        (ValueError("TestCase x not found"), "TestCase x not found", 404),
        (ValueError("Test case is not approved"), "Test case is not approved", 400),
        (RuntimeError("LLM down"), "Script generation failed: LLM down", 500),
    ],
)
def test_failed_job_keeps_the_synchronous_route_error(error, message, error_status):
    generator = _Generator(result=error)
    generator.release.set()
    jobs = ScriptGenerationJobs(generator)
    project_id = uuid4()

    job = _wait_until_finished(jobs, project_id, jobs.start(project_id, uuid4(), "Playwright").id)

    assert job.status == FAILED
    assert job.error == message
    assert job.error_status == error_status


def test_job_is_hidden_from_other_projects_and_pruned_after_ttl():
    generator = _Generator()
    generator.release.set()
    jobs = ScriptGenerationJobs(generator, finished_ttl_seconds=0)
    project_id = uuid4()
    job = _wait_until_finished(jobs, project_id, jobs.start(project_id, uuid4(), "Playwright").id)

    assert jobs.get(uuid4(), job.id) is None
    time.sleep(0.01)
    jobs.start(project_id, uuid4(), "Playwright")  # starting a job prunes finished ones
    assert jobs.get(project_id, job.id) is None


@pytest.fixture
def client(monkeypatch):
    generator = _Generator()
    monkeypatch.setattr(main, "script_generation_jobs", ScriptGenerationJobs(generator))
    main.app.dependency_overrides[get_current_user] = lambda: object()
    try:
        yield TestClient(main.app), generator
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
        generator.release.set()


def test_routes_start_a_job_and_report_the_generated_test_case(client):
    http, generator = client
    project_id, test_case_id = uuid4(), uuid4()

    started = http.post(
        f"/api/v1/projects/{project_id}/testcases/{test_case_id}/generate-script/jobs",
        json={"framework": "Playwright"},
    )

    assert started.status_code == 202
    body = started.json()
    assert body["status"] == RUNNING
    assert body["test_case"] is None
    url = f"/api/v1/projects/{project_id}/script-generation-jobs/{body['job_id']}"
    assert http.get(url).json()["status"] == RUNNING

    generator.release.set()
    _wait_until_finished(main.script_generation_jobs, project_id, main.UUID(body["job_id"]))
    done = http.get(url).json()
    assert done["status"] == COMPLETED
    assert done["test_case"]["title"] == "Valid user logs in"
    assert done["error"] is None


def test_unknown_job_is_404(client):
    http, _ = client

    response = http.get(f"/api/v1/projects/{uuid4()}/script-generation-jobs/{uuid4()}")

    assert response.status_code == 404
    assert "generate the script again" in response.json()["detail"]
