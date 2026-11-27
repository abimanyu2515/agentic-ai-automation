"""Accessibility Agent, slice 3 (spec 0011): accessibility bug drafts (AC-6), Jira
labels and the element screenshot (AC-7), the screenshot route (AC-12), and the
bug store lookups that keep functional and accessibility bugs apart."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.agents.bug_reporting_agent import BugReportingAgent
from backend.models.accessibility import AccessibilityReport, AccessibilityReportStatus, AccessibilitySource
from backend.models.bug_report import ACCESSIBILITY_CATEGORY, BugReport, BugReportStatus
from backend.models.state import WorkflowState
from backend.repository.accessibility_repository import InMemoryAccessibilityReportRepository
from backend.repository.bug_report_repository import InMemoryBugReportRepository
from backend.services.accessibility_service import safe_screenshot_name, screenshot_file
from backend.services.bug_report_service import BugReportService
from backend.tests.conftest import OfflineAnalysisLLM
from backend.tests.test_accessibility import (  # noqa: F401  (fixtures)
    SECRET,
    FakeVault,
    _raw,
    _result,
    _service,
    api,
    repo,
    world,
)


class FakeJira:
    def __init__(self):
        self.config = SimpleNamespace(base_url="https://jira.example.test", project_key="CFG")
        self.created, self.comments, self.uploads = [], [], []

    def search_issues(self, jql):
        return []

    def create_issue(self, **fields):
        self.created.append(fields)
        return {"key": f"SHOP-{len(self.created)}", "url": "https://jira.example.test/browse/SHOP"}

    def add_comment(self, key, text):
        self.comments.append((key, text))
        return True

    def link_issues(self, inward, outward, link_type="Relates"):
        return True

    def upload_attachment(self, key, filename, content, mime_type):
        self.uploads.append((key, filename, mime_type))
        return True


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    import backend.services.playwright_runner as runner

    root = tmp_path / "artifacts"
    monkeypatch.setattr(runner, "ARTIFACTS_ROOT", root)
    return root


@pytest.fixture
def bugs(repo, world, artifacts):
    """An accessibility service and a bug service sharing their stores."""
    a11y_store, bug_store, jira = InMemoryAccessibilityReportRepository(), InMemoryBugReportRepository(), FakeJira()
    bug_service = BugReportService(
        llm_service=OfflineAnalysisLLM(), bug_repo=bug_store, project_repo=repo, jira_service=jira,
        vault=FakeVault(), accessibility_repo=a11y_store,
    )
    return SimpleNamespace(
        a11y=_service(repo, a11y_store), a11y_store=a11y_store, bug_service=bug_service,
        bug_store=bug_store, jira=jira, artifacts=artifacts, **vars(world),
    )


def _scanned(bugs, repo, raw=None):
    result = _result(bugs.case)
    repo.save_execution_result(bugs.project.id, result)
    return result, bugs.a11y.process(bugs.project.id, result, raw or _raw())


def _draft(bugs, report, result=None):
    return bugs.bug_service.draft_from_accessibility(bugs.project.id, report, result=result)


def _with_shot(raw, rule_index, name):
    raw["results"][0]["violations"][rule_index]["nodes"][0]["screenshot"] = name
    return raw


# ---------------------------------------------------------------- drafting (AC-6)


def test_serious_and_critical_violations_become_accessibility_drafts(bugs, repo):
    result, report = _scanned(bugs, repo)
    drafted = _draft(bugs, report, result)

    # image-alt (critical) and label (serious). Moderate and needs_review never draft.
    assert sorted(b.a11y_rule_id for b in drafted) == ["image-alt", "label"]
    by_rule = {b.a11y_rule_id: b for b in drafted}
    assert all(b.failure_category == ACCESSIBILITY_CATEGORY for b in drafted)
    assert by_rule["image-alt"].severity == "critical" and by_rule["label"].severity == "major"
    label = by_rule["label"]
    assert label.execution_id == result.id and label.test_case_id == bugs.case.id
    assert label.accessibility_report_id == report.id and label.wcag_criteria == ["4.1.2", "1.3.1"]
    assert label.environment["page_url"] == "https://shop.example.test/"
    assert "Open cart" in label.title
    assert SECRET not in label.model_dump_json()

    links = {f.rule_id: f.bug_report_id for f in bugs.a11y_store.get(report.id).findings}
    assert links["label"] == label.id and links["image-alt"] == by_rule["image-alt"].id
    assert links["region"] is None and links["color-contrast"] is None
    # The run's functional bug fields are left alone.
    assert result.bug_report_id is None and result.jira_bug_id is None


def test_threshold_critical_drafts_only_critical(bugs, repo):
    bugs.project.a11y_block_severity = "critical"
    repo.update_project(bugs.project)
    result, report = _scanned(bugs, repo)
    assert [b.a11y_rule_id for b in _draft(bugs, report, result)] == ["image-alt"]


def test_failed_scan_drafts_nothing(bugs, repo):
    result, report = _scanned(bugs, repo, _raw(status="failed"))
    assert report.status == AccessibilityReportStatus.FAILED
    assert _draft(bugs, report, result) == []


def test_one_open_bug_per_signature_across_runs(bugs, repo):
    first_result, first = _scanned(bugs, repo)
    first_bugs = _draft(bugs, first, first_result)
    # A retried webhook replaces the same report: no extra occurrence.
    _draft(bugs, first, first_result)
    second_result, second = _scanned(bugs, repo)
    second_bugs = _draft(bugs, second, second_result)

    assert {b.id for b in first_bugs} == {b.id for b in second_bugs}
    assert len(bugs.bug_store.list_reports(bugs.project.id)) == 2
    assert all(len(b.occurrences) == 2 for b in second_bugs)
    assert {o.accessibility_report_id for o in second_bugs[0].occurrences} == {first.id, second.id}
    links = {f.rule_id: f.bug_report_id for f in bugs.a11y_store.get(second.id).findings}
    assert links["label"] and links["image-alt"]


def test_repeat_of_a_filed_bug_comments_in_jira(bugs, repo):
    result, report = _scanned(bugs, repo)
    label = next(b for b in _draft(bugs, report, result) if b.a11y_rule_id == "label")
    bugs.bug_service.submit(bugs.project.id, label.id, actor="qa")

    again_result, again = _scanned(bugs, repo)
    _draft(bugs, again, again_result)

    assert [key for key, _ in bugs.jira.comments] == ["SHOP-1"]
    assert "occurrence 2" in bugs.jira.comments[0][1]


def test_dismissed_bug_ignores_repeats_and_resolved_gets_a_new_draft(bugs, repo):
    result, report = _scanned(bugs, repo)
    drafted = {b.a11y_rule_id: b for b in _draft(bugs, report, result)}
    bugs.bug_service.dismiss(bugs.project.id, drafted["label"].id, "Known, out of scope", actor="qa")
    resolved = drafted["image-alt"]
    resolved.status = BugReportStatus.RESOLVED
    bugs.bug_store.save(resolved)

    again_result, again = _scanned(bugs, repo)
    redrafted = _draft(bugs, again, again_result)

    assert [b.a11y_rule_id for b in redrafted] == ["image-alt"]
    assert redrafted[0].id != resolved.id and redrafted[0].status == BugReportStatus.DRAFT
    links = {f.rule_id: f.bug_report_id for f in bugs.a11y_store.get(again.id).findings}
    assert links["label"] is None and links["image-alt"] == redrafted[0].id


def test_functional_lookups_leave_out_accessibility_bugs(bugs, repo):
    result, report = _scanned(bugs, repo)
    a11y_bug = _draft(bugs, report, result)[0]
    store = bugs.bug_store

    assert store.get_by_execution(bugs.project.id, result.id) is None
    assert store.find_open_by_signature(bugs.project.id, bugs.case.id, a11y_bug.error_signature) is None
    assert store.find_open_a11y_by_signature(bugs.project.id, a11y_bug.error_signature).id == a11y_bug.id


def test_a_bug_needs_a_run_or_an_accessibility_report():
    with pytest.raises(ValueError):
        BugReport(project_id=uuid4(), title="t")
    scan_bug = BugReport(project_id=uuid4(), accessibility_report_id=uuid4(), title="t")
    assert scan_bug.execution_id is None and scan_bug.test_case_id is None


def test_scan_report_bug_uses_the_page_url_and_no_test_label(bugs):
    report = AccessibilityReport(project_id=bugs.project.id, source=AccessibilitySource.SCAN)
    bugs.a11y._fill(report, _raw(), [])
    report.status = AccessibilityReportStatus.SUCCEEDED
    bugs.a11y_store.save(report)

    label = next(b for b in _draft(bugs, report) if b.a11y_rule_id == "label")

    assert label.execution_id is None and label.test_case_id is None
    assert "https://shop.example.test/" in label.title
    assert label.environment["target_url"] == "https://shop.example.test"
    labels = bugs.bug_service._labels(label)
    assert not any(name.startswith("test_") for name in labels)
    assert {"accessibility", "a11y_label", "wcag_4.1.2", "wcag_1.3.1"} <= set(labels)


# ---------------------------------------------------------------- Jira (AC-7)


def test_submit_adds_labels_and_the_element_screenshot(bugs, repo):
    shots = bugs.artifacts / str(bugs.case.id) / "a11y-shots"
    shots.mkdir(parents=True)
    (shots / "label-abc123.png").write_bytes(b"\x89PNG fake")
    result, report = _scanned(bugs, repo, _with_shot(_raw(), 1, "label-abc123.png"))
    label = next(b for b in _draft(bugs, report, result) if b.a11y_rule_id == "label")
    assert label.attachments == [str((shots / "label-abc123.png").resolve())]

    submitted = bugs.bug_service.submit(bugs.project.id, label.id, actor="qa")

    assert submitted.status == BugReportStatus.SUBMITTED
    created = bugs.jira.created[-1]
    assert {"accessibility", "a11y_label", "wcag_4.1.2", "wcag_1.3.1", f"test_{bugs.case.id}"} <= set(created["labels"])
    assert "does not certify WCAG conformance" in created["description"]
    assert bugs.jira.uploads == [(submitted.jira_key, "label-abc123.png", "image/png")]
    # The run's Jira link belongs to its functional bug and stays empty.
    assert repo.get_execution_result(bugs.project.id, result.id).jira_bug_id is None


def test_auto_submit_follows_the_project_setting(bugs, repo):
    bugs.project.bug_auto_submit = True
    repo.update_project(bugs.project)
    result, report = _scanned(bugs, repo)
    drafted = _draft(bugs, report, result)
    assert {b.status for b in drafted} == {BugReportStatus.SUBMITTED}
    assert len(bugs.jira.created) == 2


def test_bug_reporting_agent_drafts_from_accessibility_results(bugs, repo):
    result, report = _scanned(bugs, repo)
    state = WorkflowState(project_id=bugs.project.id, execution_results=[result], accessibility_results=[report])

    state = BugReportingAgent(bug_report_service=bugs.bug_service).run(state)

    assert len(state.bug_report_ids) == 2
    assert any("2 accessibility bug(s)" in line for line in state.logs)


# ---------------------------------------------------------------- screenshots (AC-12)


def test_merge_keeps_only_safe_screenshot_names(bugs, repo):
    raw = _with_shot(_with_shot(_raw(), 0, "../../secret.png"), 1, "label-1.png")
    _, report = _scanned(bugs, repo, raw)
    shots = {f.rule_id: f.screenshot_path for f in report.findings}
    assert shots["image-alt"] is None and shots["label"] == "label-1.png"
    assert safe_screenshot_name("a/b.png") is None and safe_screenshot_name("a\\b.png") is None
    assert safe_screenshot_name("..png") is None and safe_screenshot_name("x.jpg") is None
    assert screenshot_file(report, "label-1.png") is None  # no file on disk


def test_screenshot_route(api, repo, artifacts):
    result = _result(api.case)
    repo.save_execution_result(api.project.id, result)
    report = api.service.process(api.project.id, result, _with_shot(_raw(), 1, "label-1.png"))
    shots = artifacts / str(api.case.id) / "a11y-shots"
    shots.mkdir(parents=True)
    (shots / "label-1.png").write_bytes(b"\x89PNG fake")
    (shots / "other.png").write_bytes(b"\x89PNG other")
    (artifacts / "secret.png").write_bytes(b"secret")
    base = f"/api/v1/projects/{api.project.id}/accessibility/reports/{report.id}/screenshots"

    ok = api.client.get(f"{base}/label-1.png")
    assert ok.status_code == 200 and ok.content == b"\x89PNG fake"
    assert api.client.get(f"{base}/..%2Fsecret.png").status_code == 404
    assert api.client.get(f"{base}/..%5C..%5Csecret.png").status_code == 404
    assert api.client.get(f"{base}/other.png").status_code == 404  # no finding records it
    other = repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    other_url = f"/api/v1/projects/{other.id}/accessibility/reports/{report.id}/screenshots/label-1.png"
    assert api.client.get(other_url).status_code == 404


# ---------------------------------------------------------------- Postgres store


def test_postgres_bug_store_a11y_lookups(tmp_path, monkeypatch):
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
    scan_bug = BugReport(project_id=project_id, accessibility_report_id=uuid4(), title="a11y",
                         failure_category=ACCESSIBILITY_CATEGORY, error_signature="sig")
    functional = BugReport(project_id=project_id, execution_id=uuid4(), test_case_id=uuid4(), title="f",
                           failure_category="Product Bug", error_signature="sig")
    store.save(scan_bug)
    store.save(functional)

    assert store.find_open_a11y_by_signature(project_id, "sig").id == scan_bug.id
    assert store.find_open_by_signature(project_id, functional.test_case_id, "sig").id == functional.id
    scan_bug.status = BugReportStatus.DISMISSED
    store.save(scan_bug)
    assert store.find_open_a11y_by_signature(project_id, "sig") is None
    assert store.find_latest_a11y_by_signature(project_id, "sig").status == BugReportStatus.DISMISSED
    engine.dispose()
