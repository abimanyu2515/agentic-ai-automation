"""Defects read from Jira as knowledge entries (spec 0006, AC-14a, AC-15, AC-16).

A fake JiraService stands in for Jira, so no test calls it. `mock_mode` is
always true under pytest on the real service, so the fake sets it to False.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import get_current_user, get_db
from backend.main import app
from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.knowledge import ENTRY_FAILED, ENTRY_INDEXED, KnowledgeEntry
from backend.models.object_repository import ProjectObjectRepository
from backend.repository import project_repository as repo_module
from backend.services import knowledge_indexer as indexer_module
from backend.services.knowledge_indexer import KnowledgeIndexer, queue_defect_keys
from backend.services.knowledge_sources import (
    SourceUnavailable,
    defect_text,
    list_project_sources,
    read_defect,
)
from backend.tests.test_knowledge_base import FakeClock, kb, settings  # noqa: F401  (pytest fixtures)
from backend.tests.test_knowledge_entries import _story, _test_case


class FakeJira:
    def __init__(self, issues=None, mock_mode=False):
        self.issues = issues or {}
        self.mock_mode = mock_mode
        self.calls: list[str] = []

    def get_issue(self, key):
        self.calls.append(key)
        return self.issues.get(key)


def _issue(summary="Discount banner missing", status="Open", priority="High", description="Members see no banner."):
    fields = {"summary": summary, "status": {"name": status}, "priority": {"name": priority}, "description": description}
    return {"key": "BUG-1", "fields": fields}


@pytest.fixture
def dk(kb):
    """kb plus an indexer that reads defects through a fake Jira."""
    jira = FakeJira({"BUG-1": _issue()})
    indexer = KnowledgeIndexer(
        retriever=kb.retriever, repository=kb.repo, settings=kb.settings, clock=kb.clock, jira_service=jira
    )
    return SimpleNamespace(kb=kb, jira=jira, indexer=indexer, repo=kb.repo)


def _failed_run(repo, project, case, bug="BUG-1"):
    result = ExecutionResult(test_case_id=case.id, status=ExecutionStatus.FAILED, error_message="boom", jira_bug_id=bug)
    repo.save_execution_result(project.id, result)
    return result


def _run(dk, delays=()):
    for delay in [0, *delays]:
        dk.kb.clock.advance(delay)
        dk.indexer.run_pending()


def _entry(dk, project, key="BUG-1"):
    return dk.repo.get_knowledge_entry(project.id, "defect", key)


# ------------------------------------------------------------ indexing and isolation


def test_a_bug_on_a_live_execution_is_indexed_and_stays_in_its_project(dk):
    a, b = dk.kb.project("A", "https://a.example.com"), dk.kb.project("B", "https://b.example.com")
    _failed_run(dk.repo, a, _test_case(dk.kb, _story(dk.kb, a)))

    dk.indexer.enqueue_source(a.id, "defect", "BUG-1")
    dk.indexer.enqueue_source(b.id, "defect", "BUG-1")
    _run(dk)

    entry = _entry(dk, a)
    assert entry.status == ENTRY_INDEXED and entry.title == "Discount banner missing"
    assert _entry(dk, b) is None
    items = dk.kb.service.search(dk.repo.get_project(a.id), "discount banner missing", source_types=["defect"])
    assert items[0]["source_ref"] == "BUG-1" and items[0]["title"] == "Discount banner missing"
    assert dk.jira.calls == ["BUG-1"]  # project B never called Jira: no link there


def test_the_defect_text_has_key_summary_status_priority_and_description():
    source = defect_text("BUG-1", _issue())
    assert source.text == (
        "Defect BUG-1: Discount banner missing\nStatus: Open\nPriority: High\nDescription:\nMembers see no banner."
    )


def test_field_shapes_never_fail_and_empty_lines_are_left_out():
    rich = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Banner"}, {"type": "text", "text": "missing"}]}]}
    assert "Description:\nBanner missing" in defect_text("BUG-1", {"fields": {"summary": "s", "description": rich}}).text
    bare = defect_text("BUG-1", {"fields": {"summary": None, "description": None, "status": None}})
    assert bare.text == "Defect BUG-1" and bare.title == "BUG-1"
    assert defect_text("BUG-1", {}).text == "Defect BUG-1"
    long = defect_text("BUG-1", {"fields": {"description": "x" * 5000}})
    assert long.text.endswith("x" * 4000) and "x" * 4001 not in long.text


def test_the_same_issue_twice_uploads_once(dk):
    project = dk.kb.project()
    _failed_run(dk.repo, project, _test_case(dk.kb, _story(dk.kb, project)))
    uploads = []
    original = dk.kb.retriever.upload_text
    dk.kb.retriever.upload_text = lambda p, text, name: uploads.append(name) or original(p, text, name)

    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk)
    assert uploads == ["defect__BUG-1.txt"]


def test_a_closed_bug_stays_indexed_with_its_new_status(dk):
    project = dk.kb.project()
    _failed_run(dk.repo, project, _test_case(dk.kb, _story(dk.kb, project)))
    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk)
    first = _entry(dk, project)

    dk.jira.issues["BUG-1"] = _issue(status="Closed")
    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk)
    second = _entry(dk, project)
    assert second.status == ENTRY_INDEXED and second.content_hash != first.content_hash
    assert second.remote_doc_id != first.remote_doc_id


# ------------------------------------------------------------ links


def test_keys_are_trimmed_and_matched_exactly(dk):
    project = dk.kb.project()
    case = _test_case(dk.kb, _story(dk.kb, project))
    _failed_run(dk.repo, project, case, bug="  BUG-1 ")
    assert read_defect(dk.repo, project, "BUG-1", dk.jira) is not None
    assert read_defect(dk.repo, project, "bug-1", dk.jira) is None
    assert read_defect(dk.repo, project, "  ", dk.jira) is None


def test_a_key_linked_only_from_a_test_case_is_found(dk):
    project = dk.kb.project()
    case = _test_case(dk.kb, _story(dk.kb, project))
    dk.repo.update_test_case(case.model_copy(update={"jira_issue_key": "BUG-1"}))
    assert read_defect(dk.repo, project, "BUG-1", dk.jira).title == "Discount banner missing"


def test_removing_the_last_link_removes_the_entry_and_one_of_two_keeps_it(dk):
    project = dk.kb.project()
    case = _test_case(dk.kb, _story(dk.kb, project))
    first = _failed_run(dk.repo, project, case)
    second = _failed_run(dk.repo, project, case)
    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk)
    remote = _entry(dk, project).remote_doc_id

    dk.repo.delete_execution(first.id)  # JSON store: a soft delete no longer counts as a link
    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk)
    assert _entry(dk, project) is not None

    dk.repo.delete_execution(second.id)
    dk.jira.issues.clear()  # the link check runs before any Jira read
    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk)
    assert _entry(dk, project) is None
    assert remote not in dk.kb.retriever.datasets[dk.repo.get_project(project.id).rag_dataset_id]


def test_an_unlinked_failed_entry_is_removed(dk):
    project = dk.kb.project()
    dk.repo.save_knowledge_entry(KnowledgeEntry(
        project_id=project.id, source_type="defect", source_ref="BUG-9", title="BUG-9", content_hash="", status=ENTRY_FAILED,
    ))
    dk.indexer.enqueue_source(project.id, "defect", "BUG-9")
    _run(dk)
    assert _entry(dk, project, "BUG-9") is None and dk.jira.calls == []


# ------------------------------------------------------------ Jira failures and mock mode


def test_a_failed_jira_read_is_retried_then_failed_and_never_removes_an_entry(dk):
    project = dk.kb.project()
    _failed_run(dk.repo, project, _test_case(dk.kb, _story(dk.kb, project)))
    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk)
    indexed = _entry(dk, project)

    dk.jira.issues.clear()  # get_issue returns None, as on a network error
    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk, dk.kb.settings.rag.index_retry_delays)

    entry = _entry(dk, project)
    assert entry.status == ENTRY_FAILED and "could not read BUG-1" in entry.error
    assert entry.remote_doc_id == indexed.remote_doc_id and entry.content_hash == indexed.content_hash
    assert dk.jira.calls.count("BUG-1") == 1 + 1 + len(dk.kb.settings.rag.index_retry_delays)
    assert dk.indexer.pending() == 0


def test_a_failed_read_with_no_entry_yet_records_a_failed_entry(dk):
    project = dk.kb.project()
    _failed_run(dk.repo, project, _test_case(dk.kb, _story(dk.kb, project)))
    dk.jira.issues.clear()
    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk, dk.kb.settings.rag.index_retry_delays)
    entry = _entry(dk, project)
    assert entry.status == ENTRY_FAILED and entry.remote_doc_id is None and entry.title == "BUG-1"


def test_mock_mode_writes_no_entry_and_uploads_nothing(dk):
    project = dk.kb.project()
    _failed_run(dk.repo, project, _test_case(dk.kb, _story(dk.kb, project)))
    dk.jira.mock_mode = True
    with pytest.raises(SourceUnavailable) as raised:
        read_defect(dk.repo, project, "BUG-1", dk.jira)
    assert raised.value.transient is False

    dk.indexer.enqueue_source(project.id, "defect", "BUG-1")
    _run(dk)
    assert _entry(dk, project) is None and dk.jira.calls == []
    assert dk.indexer.stats["skipped"] == 1 and dk.indexer.pending() == 0


# ------------------------------------------------------------ reindex sources


def test_reindex_sources_include_live_defect_keys_once(dk):
    project = dk.kb.project()
    case = _test_case(dk.kb, _story(dk.kb, project))
    dk.repo.update_test_case(case.model_copy(update={"jira_issue_key": "BUG-1"}))
    _failed_run(dk.repo, project, case, bug=" BUG-1")
    gone = _failed_run(dk.repo, project, case, bug="BUG-2")
    dk.repo.delete_execution(gone.id)
    empty_pages = SimpleNamespace(load=lambda project_id: ProjectObjectRepository(project_id=project_id))

    defects = [ref for kind, ref in list_project_sources(dk.repo, project, empty_pages) if kind == "defect"]
    assert defects == ["BUG-1"]


# ------------------------------------------------------------ hooks


class Recorder:
    def __init__(self):
        self.jobs = []

    def enqueue_source(self, project_id, source_type, ref):
        self.jobs.append((project_id, source_type, str(ref)))

    def enqueue_source_delete(self, project_id, source_type, ref):
        self.jobs.append((project_id, f"remove:{source_type}", str(ref)))


@pytest.fixture
def hooks(tmp_path, monkeypatch):
    from backend.repository.project_repository import JSONProjectRepository

    repo = JSONProjectRepository(file_path=tmp_path / "hooks.json")
    monkeypatch.setattr(repo_module, "_active_project_repo", repo)
    recorder = Recorder()
    monkeypatch.setattr(indexer_module, "get_knowledge_indexer", lambda: recorder)
    app.dependency_overrides[get_db] = lambda: None
    # The delete routes check auth; with no database, sign in as a Superadmin directly.
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="Superadmin", id=uuid4())
    yield SimpleNamespace(repo=repo, jobs=recorder.jobs, client=TestClient(app))
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def _defect_jobs(hooks):
    return [(p, ref) for p, kind, ref in hooks.jobs if kind == "defect"]


def test_queue_defect_keys_trims_dedupes_and_ignores_empty(hooks):
    project_id = "p"
    queue_defect_keys(project_id, [" BUG-1", "BUG-1", None, "", "  ", "BUG-2"])
    assert _defect_jobs(hooks) == [("p", "BUG-1"), ("p", "BUG-2")]


def _seed(repo):
    project = repo.create_project(name="A", description="d", target_url="https://a.example.com")
    requirement = repo.create_requirement(
        project_id=project.id, title="R", description="d", priority="medium", business_domain="shop", requirement_id="R-1",
    )
    from backend.models.scenario import Scenario
    from backend.models.test_case import TestCase

    scenario = Scenario(requirement_id=requirement.id, scenario_name="S", description="d", approved=True)
    repo.save_scenarios([scenario])
    case = TestCase(scenario_id=scenario.id, title="T", steps=["a"], expected_result="b", jira_issue_key="BUG-7")
    repo.save_test_cases([case])
    result = ExecutionResult(test_case_id=case.id, status=ExecutionStatus.FAILED, jira_bug_id="BUG-1")
    repo.save_execution_result(project.id, result)
    return project, case, result


def test_the_execution_delete_route_queues_the_key_it_read_first(hooks):
    project, _, result = _seed(hooks.repo)
    response = hooks.client.delete(f"/api/v1/projects/{project.id}/executions/{result.id}")
    assert response.status_code == 200
    assert _defect_jobs(hooks) == [(project.id, "BUG-1")]


def test_the_execution_delete_route_queues_the_removal_of_its_failure_entry(hooks):
    # covers: AC-14 (task 12b): deleting an execution removes its execution_failure entry.
    project, _, result = _seed(hooks.repo)

    response = hooks.client.delete(f"/api/v1/projects/{project.id}/executions/{result.id}")

    assert response.status_code == 200
    assert (project.id, "remove:execution_failure", str(result.id)) in hooks.jobs


@pytest.mark.parametrize("status, queued", [("failed", True), ("timed_out", True), ("passed", False)])
def test_the_workspace_webhook_queues_only_failed_runs_for_knowledge(hooks, monkeypatch, status, queued):
    # covers: AC-14, AC-15 (task 12b): the UI run save path through the workspace webhook.
    import backend.services.execution_service as execution_service

    monkeypatch.setattr(execution_service, "emit_execution_completed", lambda *args: None)
    project, case, _ = _seed(hooks.repo)
    execution_id = str(uuid4())

    response = hooks.client.post(
        f"/api/v1/projects/{project.id}/executions/webhook",
        json={"event": "completed", "execution_id": execution_id, "test_case_id": str(case.id),
              "status": status, "error_message": None if status == "passed" else "Pay button missing"},
    )

    assert response.status_code == 200
    failure_jobs = [job for job in hooks.jobs if job[1] == "execution_failure"]
    assert failure_jobs == ([(project.id, "execution_failure", execution_id)] if queued else [])


def test_the_failed_executions_delete_queues_the_deleted_rows_keys(hooks):
    project, _, _ = _seed(hooks.repo)
    response = hooks.client.delete(f"/api/v1/projects/{project.id}/executions/failed")
    assert response.status_code == 200
    assert _defect_jobs(hooks) == [(project.id, "BUG-1")]


def test_the_test_case_delete_route_queues_its_bug_key(hooks):
    project, case, _ = _seed(hooks.repo)
    response = hooks.client.delete(f"/api/v1/projects/{project.id}/testcases/{case.id}")
    assert response.status_code == 200
    assert _defect_jobs(hooks) == [(project.id, "BUG-7")]


def test_the_scenario_delete_route_removes_its_test_cases_from_knowledge(hooks):
    # Review 2026-11-18: a deleted scenario soft deletes its test cases, so their entries must leave the index.
    project, case, _ = _seed(hooks.repo)
    response = hooks.client.delete(f"/api/v1/projects/{project.id}/scenarios/{case.scenario_id}")
    assert response.status_code == 200
    assert (project.id, "remove:test_case", str(case.id)) in hooks.jobs
    assert _defect_jobs(hooks) == [(project.id, "BUG-7")]


def test_the_jira_webhook_reindexes_every_linked_project_before_the_status_filter(hooks):
    project, _, _ = _seed(hooks.repo)
    other = hooks.repo.create_project(name="B", description="d", target_url="https://b.example.com")
    payload = {"issue": {"key": "BUG-1", "fields": {"status": {"name": "In Progress"}}}}
    response = hooks.client.post("/api/v1/webhooks/jira", json=payload)
    assert response.json()["status"] == "ignored"  # not a resolved status, but still reindexed
    assert _defect_jobs(hooks) == [(project.id, "BUG-1")]
    assert all(p != other.id for p, _ in _defect_jobs(hooks))
