"""Bug Reporting Agent: BugReportService, BugReportingAgent, stores and routes."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.agents.bug_reporting_agent import BugReportingAgent
from backend.models.bug_report import BugReport, BugReportStatus
from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase
from backend.repository.bug_report_repository import InMemoryBugReportRepository, JsonBugReportRepository
from backend.repository.project_repository import JSONProjectRepository
from backend.services.bug_report_service import (
    PRODUCT_BUG,
    BugReportError,
    BugReportService,
    _BugReportDraft,
    _DuplicateSearch,
)
from backend.tests.conftest import OfflineAnalysisLLM, StubLLMService

SECRET = "hunter2-password"
DRAFT = _BugReportDraft(
    title="Refund button ignores manager PIN",
    summary=f"Refund completes without the PIN {SECRET}.",
    steps_to_reproduce=["Open the order", "Click Refund"],
    expected_result="A PIN dialog blocks the refund",
    actual_result="Refund completed immediately",
    severity="MAJOR",
    priority="urgent",
)


# ---------------------------------------------------------------- doubles


class FakeJira:
    def __init__(self, hits=None, fail_create=False):
        self.config = SimpleNamespace(base_url="https://jira.example.test", project_key="CFG")
        self.hits = hits or {}
        self.fail_create = fail_create
        self.searches, self.created, self.comments, self.links, self.uploads = [], [], [], [], []

    def search_issues(self, jql):
        self.searches.append(jql)
        return [hit for needle, hit in self.hits.items() if needle in jql]

    def create_issue(self, **fields):
        if self.fail_create:
            raise RuntimeError("Jira is down")
        self.created.append(fields)
        return {"key": "SHOP-7", "url": "https://jira.example.test/browse/SHOP-7"}

    def add_comment(self, key, text):
        self.comments.append((key, text))
        return True

    def link_issues(self, inward, outward, link_type="Relates"):
        self.links.append((inward, outward, link_type))
        return True

    def upload_attachment(self, key, filename, content, mime_type):
        self.uploads.append((key, filename, mime_type))
        return True


class FakeVault:
    def get_credentials(self, project_id):
        return ["qa-user", SECRET]


@pytest.fixture
def repo(tmp_path):
    import backend.repository.project_repository as repo_module

    previous = repo_module._active_project_repo
    repo_module._active_project_repo = JSONProjectRepository(tmp_path / "store.json")
    yield repo_module._active_project_repo
    repo_module._active_project_repo = previous


@pytest.fixture
def world(repo):
    project = repo.create_project("Shop", "", framework="playwright", target_url="https://shop.example.test")
    project.jira_project_key = "SHOP"
    repo.update_project(project)
    requirement = repo.create_requirement(project.id, "Refunds", "Staff refund orders", "high", "orders")
    scenario = Scenario(
        requirement_id=requirement.id, scenario_name="Refund", description="Refund an order",
        path_type="happy_path", jira_issue_key="SHOP-1",
    )
    repo.save_scenarios([scenario])
    case = TestCase(
        scenario_id=scenario.id, title="Refund needs a PIN", steps=["Open the order", "Click Refund"],
        expected_result="A PIN dialog blocks the refund",
    )
    repo.save_test_cases([case])
    return SimpleNamespace(project=project, scenario=scenario, case=case, requirement=requirement)


def _result(case, category=PRODUCT_BUG, error="Expected PIN dialog, got 'Refund complete' after 5000ms", **extra):
    return ExecutionResult(
        test_case_id=case.id,
        status=extra.pop("status", ExecutionStatus.FAILED),
        error_message=error,
        failure_category=category,
        root_cause_summary="The refund endpoint skips the PIN check.",
        failing_step="Click Refund",
        evidence=[f"POST /refund 200 password={SECRET}"],
        confidence=extra.pop("confidence", 0.9),
        flaky_score=0.0,
        browser_version="chromium 130",
        project_config_snapshot={"project_id": "p", "target_url": "https://shop.example.test", "framework": "playwright"},
        **extra,
    )


def _service(repo, llm=None, jira=None, bug_repo=None):
    return BugReportService(
        llm_service=llm or StubLLMService(responses={_BugReportDraft: DRAFT, _DuplicateSearch: _DuplicateSearch(search_terms=["manager PIN"])}),
        bug_repo=bug_repo or InMemoryBugReportRepository(),
        project_repo=repo,
        jira_service=jira or FakeJira(),
        vault=FakeVault(),
    )


# ---------------------------------------------------------------- drafting


def test_draft_uses_triage_fields_normalizes_and_redacts(repo, world):
    service = _service(repo)
    result = _result(world.case)

    report = service.draft_from_execution(world.project.id, result)

    assert report.status == BugReportStatus.DRAFT
    assert result.bug_report_id == report.id
    assert report.title == DRAFT.title
    assert report.severity == "major" and report.priority == "medium"  # normalized
    assert SECRET not in report.summary and "[redacted]" in report.summary
    assert all(SECRET not in line for line in report.evidence)
    assert report.failing_step == "Click Refund"
    assert report.root_cause == "The refund endpoint skips the PIN check."
    assert report.scenario_id == world.scenario.id and report.requirement_id == world.requirement.id
    assert report.environment["target_url"] == "https://shop.example.test"
    assert report.environment["browser"] == "chromium 130"
    assert report.error_signature and [o.execution_id for o in report.occurrences] == [result.id]


@pytest.mark.parametrize("category", ["Test Environment Issue", "Flaky Test / Automation Issue", None])
def test_only_product_bugs_are_drafted_unless_forced(repo, world, category):
    service = _service(repo)
    assert service.draft_from_execution(world.project.id, _result(world.case, category=category)) is None
    forced = service.draft_from_execution(world.project.id, _result(world.case, category=category), force=True)
    assert forced is not None and forced.failure_category == category


def test_passed_runs_are_never_drafted(repo, world):
    result = _result(world.case, status=ExecutionStatus.PASSED)
    assert _service(repo).draft_from_execution(world.project.id, result, force=True) is None


def test_same_failure_reuses_the_open_report(repo, world):
    service = _service(repo)
    first = service.draft_from_execution(world.project.id, _result(world.case))
    # Same first line apart from the volatile timeout number.
    again = _result(world.case, error="Expected PIN dialog, got 'Refund complete' after 7000ms")
    second = service.draft_from_execution(world.project.id, again)

    assert second.id == first.id
    assert again.bug_report_id == first.id
    assert len(second.occurrences) == 2
    assert len(service.list_reports(world.project.id)) == 1


def test_a_different_failure_gets_its_own_report(repo, world):
    service = _service(repo)
    first = service.draft_from_execution(world.project.id, _result(world.case))
    other = service.draft_from_execution(world.project.id, _result(world.case, error="TypeError: total is undefined"))
    assert other.id != first.id


def test_llm_failure_falls_back_to_a_deterministic_draft(repo, world):
    service = _service(repo, llm=OfflineAnalysisLLM())
    report = service.draft_from_execution(world.project.id, _result(world.case))

    assert report.title.startswith("[Refund needs a PIN] Expected PIN dialog")
    assert report.steps_to_reproduce == ["Open the order", "Click Refund"]
    assert report.actual_result == "The refund endpoint skips the PIN check."
    assert report.severity == "major"


def test_drafting_never_raises(repo, world):
    class BrokenStore(InMemoryBugReportRepository):
        def save(self, report):
            raise RuntimeError("disk full")

    service = _service(repo, bug_repo=BrokenStore())
    assert service.draft_from_execution(world.project.id, _result(world.case)) is None


def test_auto_submit_respects_the_project_threshold(repo, world):
    world.project.bug_auto_submit = True
    world.project.bug_auto_submit_min_confidence = 0.8
    repo.update_project(world.project)
    jira = FakeJira()
    service = _service(repo, jira=jira)

    low = _result(world.case, confidence=0.5)
    assert service.draft_from_execution(world.project.id, low).status == BugReportStatus.DRAFT
    assert jira.created == []

    high = _result(world.case, error="TypeError: refund is undefined", confidence=0.95)
    report = service.draft_from_execution(world.project.id, high)
    assert report.status == BugReportStatus.SUBMITTED and report.submitted_by == "auto"
    assert (high.jira_bug_id, high.jira_bug_url) == ("SHOP-7", "https://jira.example.test/browse/SHOP-7")


# ---------------------------------------------------------------- edit, submit, dismiss


def test_update_edits_a_draft_and_redacts(repo, world):
    service = _service(repo)
    report = service.draft_from_execution(world.project.id, _result(world.case))

    updated = service.update(world.project.id, report.id, {"title": f"New {SECRET}", "severity": "critical", "id": "x"}, "u1")

    assert updated.title == "New [redacted]" and updated.severity == "critical"
    assert updated.id == report.id


def test_submit_creates_a_jira_bug_and_links_everything(repo, world, tmp_path):
    shot = tmp_path / "failure.png"
    shot.write_bytes(b"png")
    jira = FakeJira()
    service = _service(repo, jira=jira)
    result = _result(world.case, screenshot_path=str(shot))
    repo.save_execution_result(world.project.id, result)
    report = service.draft_from_execution(world.project.id, result)

    submitted = service.submit(world.project.id, report.id, actor="u1")

    assert submitted.status == BugReportStatus.SUBMITTED and submitted.jira_key == "SHOP-7"
    created = jira.created[0]
    assert created["project_key"] == "SHOP" and created["issue_type"] == "Bug"
    assert f"bugsig_{report.error_signature[:12]}" in created["labels"]
    assert f"scenario_{world.scenario.id}" in created["labels"]
    assert "*Steps to reproduce*" in created["description"]
    assert jira.links == [("SHOP-7", "SHOP-1", "Relates")]
    assert jira.uploads == [("SHOP-7", "failure.png", "image/png")]
    saved = repo.get_execution_result(world.project.id, result.id)
    assert (saved.jira_bug_id, saved.bug_report_id) == ("SHOP-7", report.id)
    case = repo.get_test_case(world.case.id)
    assert (case.jira_issue_key, case.jira_sync_status) == ("SHOP-7", "created")
    # Duplicate search was scoped to the project and open bugs.
    assert all('project = "SHOP"' in jql and "statusCategory != Done" in jql for jql in jira.searches)


def test_submit_links_a_duplicate_found_by_signature_label(repo, world):
    jira = FakeJira(hits={"labels = \"bugsig_": {"key": "SHOP-3"}})
    service = _service(repo, jira=jira)
    report = service.draft_from_execution(world.project.id, _result(world.case))

    submitted = service.submit(world.project.id, report.id, actor="u1")

    assert submitted.status == BugReportStatus.LINKED_DUPLICATE
    assert submitted.duplicate_of_jira_key == "SHOP-3" and jira.created == []
    assert jira.comments[0][0] == "SHOP-3"


def test_submit_links_a_duplicate_found_by_search_terms(repo, world):
    jira = FakeJira(hits={'text ~ "\\"manager PIN\\""': {"key": "SHOP-4"}})
    service = _service(repo, jira=jira)
    report = service.draft_from_execution(world.project.id, _result(world.case))

    assert service.submit(world.project.id, report.id, actor="u1").duplicate_of_jira_key == "SHOP-4"


def test_submit_failure_is_recorded_and_can_be_retried(repo, world):
    jira = FakeJira(fail_create=True)
    service = _service(repo, jira=jira)
    report = service.draft_from_execution(world.project.id, _result(world.case))

    failed = service.submit(world.project.id, report.id, actor="u1")
    assert failed.status == BugReportStatus.SUBMIT_FAILED and "Jira is down" in failed.submit_error

    jira.fail_create = False
    assert service.submit(world.project.id, report.id, actor="u1").status == BugReportStatus.SUBMITTED


def test_submitted_reports_cannot_be_edited_dismissed_or_resubmitted(repo, world):
    service = _service(repo)
    report = service.draft_from_execution(world.project.id, _result(world.case))
    service.submit(world.project.id, report.id, actor="u1")

    with pytest.raises(BugReportError):
        service.update(world.project.id, report.id, {"title": "x"}, "u1")
    with pytest.raises(BugReportError):
        service.dismiss(world.project.id, report.id, "nope", "u1")
    with pytest.raises(BugReportError):
        service.submit(world.project.id, report.id, actor="u1")


def test_a_repeat_failure_after_submit_comments_on_jira(repo, world):
    jira = FakeJira()
    service = _service(repo, jira=jira)
    report = service.draft_from_execution(world.project.id, _result(world.case))
    service.submit(world.project.id, report.id, actor="u1")

    again = _result(world.case)
    service.draft_from_execution(world.project.id, again)

    assert again.jira_bug_id == "SHOP-7"
    assert jira.comments[-1][0] == "SHOP-7" and "occurrence 2" in jira.comments[-1][1]


def test_dismiss_and_resolve(repo, world):
    service = _service(repo)
    dismissed = service.draft_from_execution(world.project.id, _result(world.case))
    assert service.dismiss(world.project.id, dismissed.id, "known issue", "u1").dismiss_reason == "known issue"

    report = service.draft_from_execution(world.project.id, _result(world.case, error="Other failure"))
    service.submit(world.project.id, report.id, actor="u1")
    assert service.mark_resolved_by_jira_key("SHOP-7") == 1
    assert service.get(world.project.id, report.id).status == BugReportStatus.RESOLVED


def test_get_is_scoped_to_the_project(repo, world):
    service = _service(repo)
    report = service.draft_from_execution(world.project.id, _result(world.case))
    assert service.get(uuid4(), report.id) is None
    with pytest.raises(LookupError):
        service.submit(uuid4(), report.id, actor="u1")


def test_relink_execution_moves_the_report_to_the_final_run_id(repo, world):
    service = _service(repo)
    result = _result(world.case)
    report = service.draft_from_execution(world.project.id, result)
    final_id = uuid4()

    service.relink_execution(report.id, result.id, final_id)

    moved = service.get(world.project.id, report.id)
    assert moved.execution_id == final_id and moved.occurrences[0].execution_id == final_id


# ---------------------------------------------------------------- agent


def test_agent_drafts_for_every_product_bug_and_skips_the_rest(repo, world):
    service = _service(repo)
    other_case = TestCase(scenario_id=world.scenario.id, title="Refund email", steps=["refund"], expected_result="mail")
    bug_a = _result(world.case)
    bug_b = _result(other_case, error="Mail never sent")
    flaky = _result(world.case, category="Flaky Test / Automation Issue", error="locator timeout")
    passed = _result(world.case, status=ExecutionStatus.PASSED)
    state = WorkflowState(
        project_id=world.project.id,
        generated_test_cases=[world.case, other_case],
        execution_results=[bug_a, flaky, bug_b, passed],
    )

    out = BugReportingAgent(bug_report_service=service).run(state)

    assert len(out.bug_report_ids) == 2
    assert bug_a.bug_report_id and bug_b.bug_report_id and flaky.bug_report_id is None
    assert any("started for 3 failed result(s)" in line for line in out.logs)
    assert any("skipped" in line and "Flaky" in line for line in out.logs)
    assert any("completed: 2 bug report(s)" in line for line in out.logs)


def test_agent_with_no_failures_only_logs():
    state = BugReportingAgent(bug_report_service=SimpleNamespace()).run(WorkflowState())
    assert state.bug_report_ids == [] and "nothing to report" in state.logs[-1]


def test_agent_requires_a_project_for_product_bugs(world):
    state = WorkflowState(execution_results=[_result(world.case)])
    with pytest.raises(ValueError):
        BugReportingAgent(bug_report_service=SimpleNamespace()).run(state)


# ---------------------------------------------------------------- batch triage


def test_batch_triage_drafts_after_analysis():
    from backend.services.execution_service import ExecutionService

    case = SimpleNamespace(id=uuid4(), title="Refund", steps=["a"], expected_result="b", test_data={}, test_data_sets=[])
    calls = []

    class Analysis:
        def analyze(self, project_id, result, test_case=None, retrieved_context=None):
            result.failure_category = PRODUCT_BUG
            return result

    class Drafts:
        def draft_from_execution(self, project_id, result, test_case=None):
            calls.append((result.failure_category, test_case))

    service = ExecutionService(analysis_service=Analysis(), bug_report_service=Drafts())
    service._triage(uuid4(), ExecutionResult(test_case_id=case.id, status=ExecutionStatus.FAILED), case)
    service._triage(uuid4(), ExecutionResult(test_case_id=case.id, status=ExecutionStatus.PASSED), case)
    assert calls == [(PRODUCT_BUG, case)]


# ---------------------------------------------------------------- stores


def test_json_store_round_trips_and_soft_deletes(tmp_path):
    store = JsonBugReportRepository(tmp_path / "bugs.json")
    project_id = uuid4()
    report = BugReport(project_id=project_id, execution_id=uuid4(), test_case_id=uuid4(), title="t", error_signature="abc")
    store.save(report)

    reloaded = JsonBugReportRepository(tmp_path / "bugs.json")
    assert reloaded.get(report.id).title == "t"
    assert reloaded.find_open_by_signature(project_id, report.test_case_id, "abc").id == report.id
    assert reloaded.get_by_execution(project_id, report.execution_id).id == report.id
    assert [r.id for r in reloaded.list_reports(project_id, BugReportStatus.DRAFT)] == [report.id]
    assert reloaded.list_reports(project_id, BugReportStatus.SUBMITTED) == []
    assert reloaded.soft_delete(report.id) and reloaded.get(report.id) is None


def test_json_store_never_overwrites_a_file_it_cannot_decode(tmp_path):
    """Review 2026-11-27: a damaged store must raise, not read as empty, or the
    next save replaces every bug with one."""
    path = tmp_path / "bugs.json"
    store = JsonBugReportRepository(path)
    store.save(BugReport(project_id=uuid4(), execution_id=uuid4(), test_case_id=uuid4(), title="t", error_signature="a"))
    damaged = path.read_text(encoding="utf-8")[:-10]
    path.write_text(damaged, encoding="utf-8")

    with pytest.raises(ValueError):
        store.save(BugReport(project_id=uuid4(), execution_id=uuid4(), test_case_id=uuid4(), title="u", error_signature="b"))
    assert path.read_text(encoding="utf-8") == damaged


def test_postgres_store_round_trips(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import backend.repository.postgres_bug_report_repository as module
    from backend.database.db import Base
    from backend.database.db_models import ProjectDB

    engine = create_engine(f"sqlite:///{tmp_path / 'bugs.db'}")
    tables = [Base.metadata.tables[name] for name in ("users", "projects", "releases", "bug_reports")]
    saved = {t.name: set(t.indexes) for t in tables if t.name != "bug_reports"}
    for table in tables:
        if table.name != "bug_reports":
            table.indexes.clear()
    try:
        Base.metadata.create_all(engine, tables=tables)
    finally:
        for table in tables:
            if table.name in saved:
                table.indexes.update(saved[table.name])
    monkeypatch.setattr(module, "SessionLocal", sessionmaker(bind=engine))
    project_id = uuid4()
    with sessionmaker(bind=engine)() as session:
        session.add(ProjectDB(id=project_id, name="p", description="d"))
        session.commit()

    store = module.PostgresBugReportRepository()
    report = BugReport(project_id=project_id, execution_id=uuid4(), test_case_id=uuid4(), title="t", error_signature="sig")
    store.save(report)
    report.status, report.jira_key = BugReportStatus.SUBMITTED, "SHOP-9"
    store.save(report)

    assert store.get(report.id).jira_key == "SHOP-9"
    assert store.find_open_by_signature(project_id, report.test_case_id, "sig").id == report.id
    assert [r.id for r in store.list_by_jira_key("SHOP-9")] == [report.id]
    assert [r.id for r in store.list_reports(project_id, BugReportStatus.SUBMITTED)] == [report.id]
    assert store.soft_delete(report.id) and store.get(report.id) is None
    engine.dispose()


# ---------------------------------------------------------------- routes


@pytest.fixture
def api(repo, world):
    from backend.main import app, get_bug_report_service

    jira = FakeJira()
    service = _service(repo, llm=OfflineAnalysisLLM(), jira=jira)
    app.dependency_overrides[get_bug_report_service] = lambda: service
    yield SimpleNamespace(client=TestClient(app), service=service, jira=jira, **vars(world))
    app.dependency_overrides.pop(get_bug_report_service, None)


def test_routes_report_edit_submit_and_list(api, repo):
    base = f"/api/v1/projects/{api.project.id}"
    result = _result(api.case, category="Test Environment Issue")
    repo.save_execution_result(api.project.id, result)

    created = api.client.post(f"{base}/executions/{result.id}/bug-report")
    assert created.status_code == 200, created.text
    report_id = created.json()["id"]
    assert repo.get_execution_result(api.project.id, result.id).bug_report_id is not None
    # A second request returns the same report.
    assert api.client.post(f"{base}/executions/{result.id}/bug-report").json()["id"] == report_id

    patched = api.client.patch(f"{base}/bug-reports/{report_id}", json={"title": "Refund skips PIN", "severity": "minor"})
    assert patched.status_code == 200 and patched.json()["severity"] == "minor"

    listed = api.client.get(f"{base}/bug-reports", params={"status": "draft"})
    assert [r["id"] for r in listed.json()] == [report_id]

    submitted = api.client.post(f"{base}/bug-reports/{report_id}/submit")
    assert submitted.json()["status"] == "submitted" and submitted.json()["jira_key"] == "SHOP-7"
    assert api.client.post(f"{base}/bug-reports/{report_id}/submit").status_code == 409
    assert api.client.post(f"{base}/bug-reports/{report_id}/dismiss", json={"reason": "x"}).status_code == 409

    detail = api.client.get(f"{base}/executions/{result.id}").json()
    assert detail["execution"]["bug_report_id"] == report_id


def test_routes_reject_passed_runs_and_unknown_ids(api, repo):
    base = f"/api/v1/projects/{api.project.id}"
    passed = _result(api.case, status=ExecutionStatus.PASSED)
    repo.save_execution_result(api.project.id, passed)

    assert api.client.post(f"{base}/executions/{passed.id}/bug-report").status_code == 409
    assert api.client.post(f"{base}/executions/{uuid4()}/bug-report").status_code == 404
    assert api.client.get(f"{base}/bug-reports/{uuid4()}").status_code == 404
    assert api.client.post(f"{base}/bug-reports/{uuid4()}/submit").status_code == 404


def test_route_permissions(api, repo, sign_in):
    base = f"/api/v1/projects/{api.project.id}"
    result = _result(api.case)
    repo.save_execution_result(api.project.id, result)
    stakeholder = sign_in("Stakeholder", projects=[api.project.id]).headers
    tester = sign_in("Tester", projects=[api.project.id]).headers
    outsider = sign_in("Tester").headers

    assert api.client.get(f"{base}/bug-reports", headers=stakeholder).status_code == 200
    assert api.client.post(f"{base}/executions/{result.id}/bug-report", headers=stakeholder).status_code == 403
    assert api.client.get(f"{base}/bug-reports", headers=outsider).status_code == 403
    assert api.client.post(f"{base}/executions/{result.id}/bug-report", headers=tester).status_code == 200


def test_jira_webhook_marks_bug_reports_resolved(api, webhook_headers):
    report = api.service.draft_from_execution(api.project.id, _result(api.case))
    api.service.submit(api.project.id, report.id, actor="u1")
    payload = {"issue": {"key": "SHOP-7", "fields": {"status": {"name": "Resolved"}}}}

    response = api.client.post("/api/v1/webhooks/jira", json=payload, headers=webhook_headers("jira"))

    assert response.status_code == 200 and response.json()["resolved_bug_reports"] == 1
    assert api.service.get(api.project.id, report.id).status == BugReportStatus.RESOLVED


def test_project_settings_save_bug_auto_submit(api, repo):
    url = f"/api/v1/projects/{api.project.id}"
    body = {
        "name": "Shop", "description": "", "line_of_business": "general", "framework": "playwright",
        "target_url": "https://shop.example.test", "bug_auto_submit": True, "bug_auto_submit_min_confidence": 0.7,
    }
    response = api.client.put(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["bug_auto_submit"] is True
    assert response.json()["bug_auto_submit_min_confidence"] == 0.7
    saved = repo.get_project(api.project.id)
    assert saved.bug_auto_submit is True and saved.bug_auto_submit_min_confidence == 0.7

    assert api.client.put(url, json={**body, "bug_auto_submit_min_confidence": 1.5}).status_code == 422
