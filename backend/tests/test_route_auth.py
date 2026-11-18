"""Auth on the test case, execution, settings and project delete routes (review 2026-11-18),
plus the JSON store lock now that the knowledge indexer writes from its own thread."""
import threading
from uuid import uuid4

import pytest

import backend.main as main_module
from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase
from backend.repository.project_repository import JSONProjectRepository
from backend.tests.test_knowledge_base import (  # noqa: F401  (pytest fixtures)
    _assign,
    _user,
    api,
    roles_db,
    settings,
    signed_in,
)


def _seed(repo, name):
    project = repo.create_project(name=name, description="d", target_url=f"https://{name.lower()}.example.com")
    requirement = repo.create_requirement(
        project_id=project.id, title="R", description="d", priority="medium", business_domain="shop", requirement_id="R-1",
    )
    scenario = Scenario(requirement_id=requirement.id, scenario_name="S", description="d", approved=True)
    repo.save_scenarios([scenario])
    case = TestCase(scenario_id=scenario.id, title="T", steps=["a"], expected_result="b")
    repo.save_test_cases([case])
    result = ExecutionResult(test_case_id=case.id, status=ExecutionStatus.FAILED)
    repo.save_execution_result(project.id, result)
    return project, case, result


def test_a_stakeholder_cannot_change_or_delete_project_data(api, roles_db, signed_in):
    project, case, result = _seed(api.repo, "Alpha")
    stakeholder = _user(roles_db, "Stakeholder")
    _assign(roles_db, stakeholder, project.id, "Stakeholder")
    signed_in(stakeholder)

    base = f"/api/v1/projects/{project.id}"
    responses = [
        api.client.put(f"{base}/testcases/{case.id}", json={"title": "changed"}),
        api.client.put(f"{base}/testcases/{case.id}/script", json={"script": "x"}),
        api.client.delete(f"{base}/testcases/{case.id}"),
        api.client.delete(f"{base}/executions/{result.id}"),
        api.client.delete(f"{base}/executions/failed"),
        api.client.delete(base),
    ]

    assert [r.status_code for r in responses] == [403] * 6
    assert api.repo.get_test_case(case.id).title == "T"
    assert api.repo.get_execution_result(project.id, result.id) is not None
    assert api.repo.get_project(project.id) is not None


def test_a_user_not_on_the_project_gets_403(api, roles_db, signed_in):
    project, case, result = _seed(api.repo, "Alpha")
    outsider = _user(roles_db, "Project Manager")  # holds the grants globally, but has no project access
    signed_in(outsider)

    base = f"/api/v1/projects/{project.id}"
    responses = [
        api.client.put(f"{base}/testcases/{case.id}", json={"title": "changed"}),
        api.client.delete(f"{base}/testcases/{case.id}"),
        api.client.delete(f"{base}/executions/{result.id}"),
    ]

    assert [r.status_code for r in responses] == [403] * 3
    assert api.repo.get_test_case(case.id).title == "T"


def test_access_to_one_project_never_reaches_another_projects_rows(api, roles_db, signed_in):
    mine, _, _ = _seed(api.repo, "Alpha")
    _, their_case, their_result = _seed(api.repo, "Beta")
    manager = _user(roles_db, "Project Manager")
    _assign(roles_db, manager, mine.id, "Project Manager")
    signed_in(manager)

    base = f"/api/v1/projects/{mine.id}"
    responses = [
        api.client.put(f"{base}/testcases/{their_case.id}", json={"title": "changed"}),
        api.client.put(f"{base}/testcases/{their_case.id}/script", json={"script": "x"}),
        api.client.delete(f"{base}/testcases/{their_case.id}"),
        api.client.delete(f"{base}/executions/{their_result.id}"),
    ]

    assert [r.status_code for r in responses] == [404] * 4
    assert api.repo.get_test_case(their_case.id).title == "T"


def test_a_project_manager_can_still_edit_and_delete_in_their_project(api, roles_db, signed_in):
    project, case, result = _seed(api.repo, "Alpha")
    manager = _user(roles_db, "Project Manager")
    _assign(roles_db, manager, project.id, "Project Manager")
    signed_in(manager)

    base = f"/api/v1/projects/{project.id}"
    assert api.client.put(f"{base}/testcases/{case.id}", json={"title": "changed"}).status_code == 200
    assert api.client.delete(f"{base}/executions/{result.id}").status_code == 200
    assert api.client.delete(f"{base}/testcases/{case.id}").status_code == 200


def test_only_a_superadmin_can_save_settings(api, roles_db, signed_in, monkeypatch):
    writes = []
    monkeypatch.setattr(main_module.yaml, "safe_dump", lambda data, f: writes.append(data))
    admin = _user(roles_db, "Admin")  # Admin has Settings view only
    signed_in(admin)

    payload = {"rag": {"ragflow_api_base": "https://attacker.example.com"}}
    assert api.client.put("/api/v1/settings", json=payload).status_code == 403
    assert writes == []


def test_json_writers_do_not_lose_a_write_from_another_thread(tmp_path, monkeypatch):
    """The indexer thread saves a document while a request thread saves test cases.
    With the lock, the second writer waits, so neither write is lost."""
    repo = JSONProjectRepository(file_path=tmp_path / "store.json")
    project = repo.create_project(name="Alpha", description="d", target_url="https://alpha.example.com")
    from backend.models.document import Document

    doc = Document(
        id=uuid4(), project_id=project.id, filename="a.txt", original_filename="a.txt",
        storage_path="a.txt", mime_type="text/plain", size=1,
    )
    case = TestCase(scenario_id=uuid4(), title="T", steps=["a"], expected_result="b")

    original_write = repo._write_raw
    other = threading.Thread(target=repo.save_document, args=(doc,))
    started = {"done": False}

    def write_then_let_the_other_thread_try(data):
        if not started["done"]:
            started["done"] = True
            other.start()
            other.join(timeout=0.5)  # with the lock it stays blocked; without it, it writes now
        original_write(data)

    monkeypatch.setattr(repo, "_write_raw", write_then_let_the_other_thread_try)
    repo.save_test_cases([case])
    other.join(timeout=5)

    assert repo.get_test_case(case.id) is not None
    assert [d.id for d in repo.get_documents(project.id)] == [doc.id]
