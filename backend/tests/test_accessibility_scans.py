"""Accessibility Agent, slices 4 to 6 (spec 0011): standalone scans and their routes,
the workspace webhook path, the scan-settings route, the report sections and JUnit
suite, the shared redaction helper, and the per rule caps.

Every test uses an in memory report store, a JSON project store in tmp_path, and
stub runners and LLMs, so nothing reaches a browser, an LLM, or the dev database."""

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.models.accessibility import (
    AccessibilityFinding,
    AccessibilityReport,
    AccessibilityReportStatus,
    AccessibilitySource,
    FindingKind,
    GateMode,
    GateStatus,
)
from backend.models.scenario import Scenario
from backend.models.test_case import TestCase
from backend.repository.accessibility_repository import InMemoryAccessibilityReportRepository
from backend.repository.project_repository import JSONProjectRepository
from backend.services.accessibility_report_section import (
    DISCLAIMER,
    MAX_TABLE_ROWS,
    accessibility_html_section,
    accessibility_template_context,
    append_accessibility_junit,
    draw_accessibility_pdf,
    get_accessibility_report_by_execution,
)
from backend.services.accessibility_scan_service import (
    SCAN_INPUT_FILE,
    AccessibilityScanConflict,
    AccessibilityScanInvalid,
    AccessibilityScanNotFound,
    AccessibilityScanService,
)
from backend.services.accessibility_service import AccessibilityService
from backend.services.redaction import clean, clean_html, secret_patterns

TARGET = "https://shop.example.test"
SECRET = "hunter2-password"


class FakeVault:
    def get_credentials(self, project_id):
        return ["qa-user", SECRET]


class BrokenVault:
    def get_credentials(self, project_id):
        raise RuntimeError("vault locked")


class OfflineLLM:
    """Any LLM call fails, so findings keep the axe fallback text (AC-4)."""

    def __getattr__(self, name):
        def call(*args, **kwargs):
            raise RuntimeError("no LLM in tests")
        return call


class RecordingBugs:
    def __init__(self):
        self.calls = []

    def draft_from_accessibility(self, project_id, report, result=None, test_case=None):
        self.calls.append((project_id, report, result, test_case))
        return []


def _rule(rule_id, impact, targets, tags=("wcag2a", "wcag111")):
    return {
        "id": rule_id,
        "impact": impact,
        "tags": list(tags),
        "help": f"{rule_id} help",
        "helpUrl": f"https://dequeuniversity.com/rules/axe/4.14/{rule_id}",
        "nodes": [
            {"target": [t], "html": f"<div id='{t}'>", "failureSummary": f"Fix {rule_id}", "impact": impact}
            for t in targets
        ],
    }


def _raw(violations=None, incomplete=None, url=f"{TARGET}/", **extra):
    violations = violations if violations is not None else [_rule("label", "serious", ["#q"], ("wcag2a", "wcag412"))]
    return {
        "status": "succeeded", "error": None, "wcag_tags": ["wcag2a"], "truncated": False, "elapsed_ms": 900,
        "pages": [{"url": url, "title": "Home", "scanned_at": "2026-11-26T10:00:00Z", "error": None}],
        "results": [{"url": url, "violations": violations, "incomplete": incomplete or []}],
        **extra,
    }


@pytest.fixture
def repo(tmp_path):
    import backend.repository.project_repository as repo_module

    previous = repo_module._active_project_repo
    repo_module._active_project_repo = JSONProjectRepository(tmp_path / "store.json")
    yield repo_module._active_project_repo
    repo_module._active_project_repo = previous


@pytest.fixture
def world(repo):
    project = repo.create_project("Shop", "", framework="playwright", target_url=TARGET)
    requirement = repo.create_requirement(project.id, "Cart", "Shoppers use the cart", "high", "cart")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="Cart", description="Open the cart",
                        path_type="happy_path")
    repo.save_scenarios([scenario])
    case = TestCase(scenario_id=scenario.id, title="Open cart", steps=[f"Open {TARGET}/cart?step=1"],
                    expected_result="Cart shows")
    repo.save_test_cases([case])
    return SimpleNamespace(project=project, case=case)


@pytest.fixture
def store():
    return InMemoryAccessibilityReportRepository()


def _a11y(repo, store):
    return AccessibilityService(repo=store, project_repo=repo, vault=FakeVault(), llm_service=OfflineLLM())


def _settings(**changes):
    from backend.config.settings import get_settings

    return SimpleNamespace(accessibility=get_settings().accessibility.model_copy(update=changes))


class FakeRunner:
    """Stands in for PlaywrightRunner: records the call and writes the a11y.json
    the template spec would have written."""

    def __init__(self, root: Path, raw=None, error: Exception | None = None):
        self.root, self.raw, self.error = root, raw, error
        self.calls = []

    def run(self, script, run_id, **kwargs):
        run_dir = self.root / run_id
        self.calls.append({"script": script, "run_id": run_id, "input": json.loads((run_dir / SCAN_INPUT_FILE).read_text()), **kwargs})
        if self.error:
            raise self.error
        if self.raw is not None:
            (run_dir / "a11y.json").write_text(json.dumps(self.raw), encoding="utf-8")
        return SimpleNamespace(status="passed")


@pytest.fixture
def scans(repo, world, store, tmp_path, monkeypatch):
    import backend.services.playwright_runner as playwright_runner

    monkeypatch.setattr(playwright_runner, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    AccessibilityScanService._pending.clear()
    runner = FakeRunner(tmp_path / "artifacts", raw=_raw())
    audits, launched, bugs = [], [], RecordingBugs()
    service = AccessibilityScanService(
        accessibility_service=_a11y(repo, store), project_repo=repo, repo=store,
        runner_factory=lambda: runner, bug_report_service=bugs,
        audit=lambda project_id, user_id, changes: audits.append(changes),
        settings=_settings(), launcher=launched.append,
    )
    yield SimpleNamespace(service=service, runner=runner, audits=audits, launched=launched, bugs=bugs,
                          store=store, repo=repo, **vars(world))
    AccessibilityScanService._pending.clear()


def _mark_safe(repo, project, **changes):
    project.is_safe_test_environment = True
    for key, value in changes.items():
        setattr(project, key, value)
    repo.update_project(project)


# ---------------------------------------------------------------- AC-10: candidate pages and paths


def test_scan_pages_lists_target_object_repository_and_test_case_pages(scans):
    from backend.services.object_repository_service import ObjectRepositoryService

    pages = ObjectRepositoryService(project_repo=scans.repo)
    pages.create_page(scans.project.id, "checkout_page", "/checkout")
    pages.create_page(scans.project.id, "product_pages", "/products/*")

    listed = scans.service.scan_pages(scans.project.id)

    by_url = {page["url"]: page for page in listed}
    assert listed[0] == {"url": TARGET, "source": "target_url"}  # covers: AC-10
    assert by_url[f"{TARGET}/checkout"]["source"] == "object_repository"
    assert by_url["/products/*"] == {"url": "/products/*", "source": "object_repository", "skipped_reason": "wildcard"}
    assert by_url[f"{TARGET}/cart?step=1"]["source"] == "test_case"


def test_scan_pages_lists_each_path_once_and_never_another_origin(scans):
    scans.case.steps = [f"Open {TARGET}/cart", f"Open {TARGET}/cart#top", "Open https://other.example.test/x"]
    scans.repo.update_test_case(scans.case)

    urls = [page["url"] for page in scans.service.scan_pages(scans.project.id)]

    assert [u for u in urls if "/cart" in u] == [f"{TARGET}/cart"]  # covers: AC-10
    assert not any("other.example.test" in u for u in urls)


def test_scan_pages_for_an_unknown_project_is_not_found(scans):
    with pytest.raises(AccessibilityScanNotFound):
        scans.service.scan_pages(uuid4())


@pytest.mark.parametrize("paths, expected", [
    ([], [TARGET]),
    (["", "   "], [TARGET]),
    (["/cart"], [f"{TARGET}/cart"]),
    (["cart"], [f"{TARGET}/cart"]),
    ([f"{TARGET}/cart#top", "/cart?x=1", "/cart/"], [f"{TARGET}/cart"]),
])
def test_resolve_paths_makes_absolute_urls_once_per_path(scans, paths, expected):
    assert scans.service.resolve_paths(TARGET, paths) == expected  # covers: AC-10


@pytest.mark.parametrize("path", [
    "https://other.example.test/x",
    "http://shop.example.test/cart",  # another scheme is another origin
    "javascript:alert(1)",
    "ftp://shop.example.test/file",
])
def test_resolve_paths_refuses_pages_off_the_project_origin(scans, path):
    with pytest.raises(AccessibilityScanInvalid) as raised:
        scans.service.resolve_paths(TARGET, [path])
    assert raised.value.status_code == 422  # covers: AC-10


def test_resolve_paths_refuses_more_pages_than_the_cap(scans):
    paths = [f"/page-{n}" for n in range(scans.service.config.max_pages + 1)]
    with pytest.raises(AccessibilityScanInvalid):
        scans.service.resolve_paths(TARGET, paths)  # covers: AC-10
    assert len(scans.service.resolve_paths(TARGET, paths[:-1])) == scans.service.config.max_pages


# ---------------------------------------------------------------- AC-10, AC-11: starting a scan


def test_start_saves_a_queued_scan_and_audits_it(scans):
    user = uuid4()
    report = scans.service.start(scans.project.id, ["/cart"], user_id=user)

    saved = scans.store.get(report.id)
    assert saved.status == AccessibilityReportStatus.QUEUED  # covers: AC-10
    assert saved.source == AccessibilitySource.SCAN and saved.created_by == user
    assert saved.login_status == "not_requested"
    assert scans.audits == [{"entity": "accessibility_scan", "report_id": str(report.id), "pages": 1,
                             "include_login": False}]


def test_a_second_scan_while_one_is_active_is_a_conflict(scans):
    scans.service.start(scans.project.id, [])
    with pytest.raises(AccessibilityScanConflict) as raised:
        scans.service.start(scans.project.id, [])
    assert raised.value.status_code == 409  # covers: AC-10


def test_the_global_off_switch_refuses_standalone_scans(scans):
    """Review 2026-11-27: `accessibility.enabled` (A11Y_ENABLED) is the kill
    switch, so it stops standalone scans too, not only test run scans."""
    scans.service._settings = _settings(enabled=False)
    assert scans.service.config.enabled is False
    with pytest.raises(AccessibilityScanConflict) as raised:
        scans.service.start(scans.project.id, [])
    assert raised.value.status_code == 409
    assert "turned off" in raised.value.detail
    assert scans.store.list_active_scans(scans.project.id) == [] and scans.launched == []


def test_login_scans_need_a_safe_test_environment(scans):
    with pytest.raises(AccessibilityScanConflict):
        scans.service.start(scans.project.id, [], include_login=True)  # covers: AC-11
    assert scans.store.list_active_scans(scans.project.id) == []

    _mark_safe(scans.repo, scans.project)
    report = scans.service.start(scans.project.id, [], include_login=True)
    # It reads failed until the scan proves the login worked.
    assert report.login_status == "failed"


def test_start_refuses_unknown_projects_and_projects_without_a_target_url(scans):
    with pytest.raises(AccessibilityScanNotFound):
        scans.service.start(uuid4(), [])
    bare = scans.repo.create_project("Bare", "", framework="playwright", target_url="")
    with pytest.raises(AccessibilityScanConflict):
        scans.service.start(bare.id, [])


def test_an_audit_failure_never_stops_a_scan(scans):
    def broken(*args):
        raise RuntimeError("audit table locked")

    scans.service.audit = broken
    report = scans.service.start(scans.project.id, [])
    assert scans.store.get(report.id).status == AccessibilityReportStatus.QUEUED


def test_launch_hands_the_job_to_the_launcher(scans):
    report = scans.service.start(scans.project.id, [])
    scans.service.launch(report.id)

    assert len(scans.launched) == 1
    scans.launched[0]()
    assert scans.store.get(report.id).status == AccessibilityReportStatus.SUCCEEDED


# ---------------------------------------------------------------- AC-10, AC-11: running a scan


def test_run_writes_the_scan_input_and_fills_the_report_in_place(scans):
    report = scans.service.start(scans.project.id, ["/cart", "/"])
    done = scans.service.run(report.id)

    call = scans.runner.calls[0]
    assert call["run_id"] == f"scan_{report.id}"
    assert call["input"]["urls"] == [f"{TARGET}/cart", f"{TARGET}/"]
    assert call["input"]["origin"] == TARGET and call["input"]["include_login"] is False
    assert "runStandaloneScan" in call["script"] and "a11y-standalone-scan" in call["script"]
    assert done.id == report.id and done.status == AccessibilityReportStatus.SUCCEEDED  # covers: AC-10
    assert done.gate_status == GateStatus.WARN and done.counts["serious"] == 1
    assert scans.store.list_reports(scans.project.id)[1] == 1


def test_scan_input_never_holds_credentials(scans):
    _mark_safe(scans.repo, scans.project)
    report = scans.service.start(scans.project.id, [], include_login=True)
    scans.service.run(report.id)

    written = json.dumps(scans.runner.calls[0]["input"])
    assert SECRET not in written and "password" not in written.lower()  # covers: AC-5, AC-11


def test_serious_scan_findings_are_handed_to_bug_drafting(scans):
    report = scans.service.start(scans.project.id, [])
    done = scans.service.run(report.id)

    assert len(scans.bugs.calls) == 1  # covers: AC-6
    assert scans.bugs.calls[0][1].id == done.id


def test_the_gate_uses_the_project_settings_from_when_the_scan_started(scans):
    scans.project.a11y_gate_mode = "block"
    scans.repo.update_project(scans.project)
    report = scans.service.start(scans.project.id, [])

    scans.project.a11y_gate_mode = "warn"
    scans.repo.update_project(scans.project)
    done = scans.service.run(report.id)

    assert done.gate_status == GateStatus.BLOCKED and done.gate_mode == GateMode.BLOCK  # covers: AC-8


@pytest.mark.parametrize("login_status, error", [("succeeded", None), ("failed", "login_failed")])
def test_login_scans_record_whether_the_login_worked(scans, login_status, error):
    _mark_safe(scans.repo, scans.project)
    scans.runner.raw = _raw(login_status=login_status)
    report = scans.service.start(scans.project.id, [], include_login=True)
    done = scans.service.run(report.id)

    assert done.login_status == login_status  # covers: AC-11
    assert done.error == error
    assert scans.runner.calls[0]["input"]["include_login"] is True


def test_pages_behind_login_stay_listed_with_their_reason(scans):
    raw = _raw()
    raw["pages"].append({"url": f"{TARGET}/account", "title": "", "scanned_at": None,
                         "requires_login": True, "error": "requires_login"})
    scans.runner.raw = raw
    report = scans.service.start(scans.project.id, [])
    done = scans.service.run(report.id)

    account = next(page for page in done.pages if page.url.endswith("/account"))
    assert account.requires_login and account.error == "requires_login"  # covers: AC-11
    assert done.status == AccessibilityReportStatus.SUCCEEDED


@pytest.mark.parametrize("runner_kwargs", [{"raw": None}, {"error": RuntimeError("browser crashed")}])
def test_a_scan_that_writes_nothing_or_crashes_is_failed_and_not_scanned(scans, runner_kwargs):
    for key, value in runner_kwargs.items():
        setattr(scans.runner, key, value)
    report = scans.service.start(scans.project.id, [])
    done = scans.service.run(report.id)

    assert done.status == AccessibilityReportStatus.FAILED  # covers: AC-2
    assert done.error == "scan_error" and done.gate_status == GateStatus.NOT_SCANNED


def test_a_page_error_only_marks_that_page(scans):
    raw = _raw()
    raw["pages"].append({"url": f"{TARGET}/strict", "title": "", "scanned_at": None, "error": "csp_blocked"})
    scans.runner.raw = raw
    report = scans.service.start(scans.project.id, [])
    done = scans.service.run(report.id)

    errors = {page.url: page.error for page in done.pages}
    assert errors == {f"{TARGET}/": None, f"{TARGET}/strict": "csp_blocked"}  # covers: AC-2
    assert done.status == AccessibilityReportStatus.SUCCEEDED and done.counts["serious"] == 1


def test_run_without_a_pending_request_changes_nothing(scans):
    stray = AccessibilityReport(project_id=scans.project.id, source=AccessibilitySource.SCAN,
                                status=AccessibilityReportStatus.QUEUED)
    scans.store.save(stray)

    assert scans.service.run(stray.id).status == AccessibilityReportStatus.QUEUED
    assert scans.runner.calls == []


# ---------------------------------------------------------------- AC-10: stale scans


def _age(store, report, minutes):
    report = store.get(report.id)
    report.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    store.save(report)


def test_stale_scans_close_as_timed_out_and_fresh_ones_stay(scans):
    other = scans.repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    old = scans.service.start(scans.project.id, [])
    fresh = scans.service.start(other.id, [])
    _age(scans.store, old, 16)

    assert scans.service.expire_stale() == 1

    closed = scans.store.get(old.id)
    assert closed.status == AccessibilityReportStatus.FAILED and closed.error == "timeout"  # covers: AC-10
    assert closed.gate_status == GateStatus.NOT_SCANNED and closed.finished_at is not None
    assert scans.store.get(fresh.id).status == AccessibilityReportStatus.QUEUED


def test_a_closed_stale_scan_frees_the_project_and_is_never_run(scans):
    old = scans.service.start(scans.project.id, [])
    _age(scans.store, old, 16)

    again = scans.service.start(scans.project.id, [])  # start closes the stale scan first

    assert scans.store.get(old.id).error == "timeout"
    assert scans.service.run(old.id).status == AccessibilityReportStatus.FAILED
    assert scans.runner.calls == []
    assert scans.store.get(again.id).status == AccessibilityReportStatus.QUEUED


def test_expire_stale_never_raises(scans):
    class BrokenStore:
        def list_active_scans(self, project_id=None):
            raise RuntimeError("store down")

    scans.service._repo = BrokenStore()
    assert scans.service.expire_stale() == 0


# ---------------------------------------------------------------- AC-10, AC-15: scan and report routes


@pytest.fixture
def api(scans):
    from backend.main import app, get_accessibility_scan_service, get_accessibility_service

    app.dependency_overrides[get_accessibility_scan_service] = lambda: scans.service
    app.dependency_overrides[get_accessibility_service] = lambda: scans.service.accessibility_service
    yield SimpleNamespace(client=TestClient(app), base=f"/api/v1/projects/{scans.project.id}/accessibility", **vars(scans))
    app.dependency_overrides.pop(get_accessibility_scan_service, None)
    app.dependency_overrides.pop(get_accessibility_service, None)


def test_scan_pages_route_returns_the_checklist_and_cap(api):
    body = api.client.get(f"{api.base}/scan-pages").json()
    assert body["pages"][0] == {"url": TARGET, "source": "target_url", "skipped_reason": None}  # covers: AC-10
    assert body["max_pages"] == api.service.config.max_pages


def test_start_scan_route_accepts_then_refuses_a_second_scan(api):
    started = api.client.post(f"{api.base}/scans", json={"paths": []})
    assert started.status_code == 202  # covers: AC-10
    assert started.json()["status"] == "queued"
    assert len(api.launched) == 1

    assert api.client.post(f"{api.base}/scans", json={"paths": []}).status_code == 409


@pytest.mark.parametrize("body", [
    {"paths": ["https://other.example.test/"]},
    {"paths": [f"/p{n}" for n in range(11)]},
    {},
])
def test_start_scan_route_rejects_bad_requests(api, body):
    assert api.client.post(f"{api.base}/scans", json=body).status_code == 422  # covers: AC-10


def test_start_scan_route_refuses_login_on_an_unsafe_project(api):
    response = api.client.post(f"{api.base}/scans", json={"paths": [], "include_login": True})
    assert response.status_code == 409  # covers: AC-11


def test_start_scan_route_for_an_unknown_project_is_404(api):
    response = api.client.post(f"/api/v1/projects/{uuid4()}/accessibility/scans", json={"paths": []})
    assert response.status_code == 404


def test_scan_routes_need_the_right_permissions(api, sign_in):
    viewer = sign_in("Stakeholder", projects=[api.project.id]).headers
    tester = sign_in("Tester", projects=[api.project.id]).headers
    outsider = sign_in("Tester").headers

    assert api.client.post(f"{api.base}/scans", json={"paths": []}, headers=viewer).status_code == 403  # covers: AC-15
    assert api.client.post(f"{api.base}/scans", json={"paths": []}, headers=outsider).status_code == 403
    assert api.client.get(f"{api.base}/reports", headers=viewer).status_code == 200
    assert api.client.get(f"{api.base}/reports", headers=outsider).status_code == 403
    assert api.client.post(f"{api.base}/scans", json={"paths": []}, headers=tester).status_code == 202


def test_scan_routes_need_a_signed_in_user():
    from backend.main import app

    client = TestClient(app)
    bad = {"Authorization": "Bearer not-a-token"}
    project = uuid4()
    assert client.post(f"/api/v1/projects/{project}/accessibility/scans", json={"paths": []}, headers=bad).status_code == 401
    assert client.get(f"/api/v1/projects/{project}/accessibility/reports", headers=bad).status_code == 401


def test_report_list_is_paged_newest_first_without_findings(api):
    old = AccessibilityReport(project_id=api.project.id, source=AccessibilitySource.SCAN,
                              created_at=datetime.now(timezone.utc) - timedelta(hours=1),
                              findings=[AccessibilityFinding(signature="s", kind=FindingKind.VIOLATION, rule_id="label")])
    new = AccessibilityReport(project_id=api.project.id, source=AccessibilitySource.EXECUTION, execution_id=uuid4())
    api.store.save(old)
    api.store.save(new)

    body = api.client.get(f"{api.base}/reports", params={"limit": 20}).json()
    assert [item["id"] for item in body["items"]] == [str(new.id), str(old.id)]  # covers: AC-15
    assert body["total"] == 2 and all("findings" not in item for item in body["items"])

    scans_only = api.client.get(f"{api.base}/reports", params={"source": "scan"}).json()
    assert [item["id"] for item in scans_only["items"]] == [str(old.id)]


@pytest.mark.parametrize("params", [{"limit": 101}, {"limit": 0}, {"offset": -1}, {"source": "other"}])
def test_report_list_rejects_bad_paging(api, params):
    assert api.client.get(f"{api.base}/reports", params=params).status_code == 422


def test_report_list_closes_stale_scans_on_read(api):
    report = api.service.start(api.project.id, [])
    _age(api.store, report, 16)

    item = api.client.get(f"{api.base}/reports").json()["items"][0]
    assert (item["status"], item["error"], item["gate_status"]) == ("failed", "timeout", "NOT_SCANNED")  # covers: AC-10


def test_one_report_route_returns_findings_and_hides_other_projects(api):
    other = api.repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    report = api.service.start(api.project.id, [])
    api.service.run(report.id)

    mine = api.client.get(f"{api.base}/reports/{report.id}")
    assert mine.status_code == 200 and mine.json()["findings"][0]["rule_id"] == "label"
    foreign = api.client.get(f"/api/v1/projects/{other.id}/accessibility/reports/{report.id}")
    assert foreign.status_code == 404  # covers: AC-15
    assert api.client.get(f"{api.base}/reports/{uuid4()}").status_code == 404


# ---------------------------------------------------------------- AC-8, AC-13: scan-settings route


def test_scan_settings_route_returns_the_project_gate_settings(repo, world, webhook_headers):
    world.project.a11y_gate_mode = "block"
    world.project.a11y_block_severity = "critical"
    repo.update_project(world.project)
    from backend.main import app

    client = TestClient(app)
    url = "/api/v1/accessibility/scan-settings"
    body = client.get(url, params={"project_id": str(world.project.id)}, headers=webhook_headers()).json()

    assert body["enabled"] is True  # covers: AC-8, AC-13
    assert (body["gate_mode"], body["block_severity"]) == ("block", "critical")
    assert body["wcag_tags"] and body["max_pages"] >= 1


def test_scan_settings_route_is_off_when_either_switch_is_off(repo, world, webhook_headers, monkeypatch):
    from backend.config.settings import get_settings
    from backend.main import app

    client = TestClient(app)
    url, params = "/api/v1/accessibility/scan-settings", {"project_id": str(world.project.id)}
    world.project.a11y_enabled = False
    repo.update_project(world.project)
    assert client.get(url, params=params, headers=webhook_headers()).json()["enabled"] is False

    world.project.a11y_enabled = True
    repo.update_project(world.project)
    monkeypatch.setattr(get_settings().accessibility, "enabled", False)
    assert client.get(url, params=params, headers=webhook_headers()).json()["enabled"] is False  # covers: AC-1


def test_scan_settings_route_needs_the_workspace_secret_and_a_project(repo, world, webhook_headers):
    from backend.main import app

    client = TestClient(app)
    url = "/api/v1/accessibility/scan-settings"
    assert client.get(url, params={"project_id": str(world.project.id)},
                      headers={"X-Webhook-Secret": "wrong"}).status_code == 401
    assert client.get(url, params={"project_id": str(world.project.id)}).status_code == 401
    assert client.get(url, params={"project_id": str(uuid4())}, headers=webhook_headers()).status_code == 404


# ---------------------------------------------------------------- AC-1, AC-2, AC-8: workspace webhook


@pytest.fixture
def workspace(repo, world, store, monkeypatch):
    import backend.services.execution_service as execution_service
    from backend.services.execution_service import ExecutionService

    monkeypatch.setattr(execution_service, "get_project_repository", lambda: repo)
    monkeypatch.setattr(execution_service, "emit_execution_completed", lambda *args: None)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_markers", lambda *args: 0)
    monkeypatch.setattr(ExecutionService, "_queue_failure_knowledge", staticmethod(lambda *args: None))
    bugs = RecordingBugs()
    service = ExecutionService(analysis_service=SimpleNamespace(analyze=lambda *a: None),
                               bug_report_service=bugs, accessibility_service=_a11y(repo, store))

    def finalize(execution_id=None, **payload):
        body = {"execution_id": str(execution_id or uuid4()), "test_case_id": str(world.case.id),
                "status": "passed", **payload}
        return service.finalize_execution(world.project.id, body)

    return SimpleNamespace(finalize=finalize, service=service, store=store, bugs=bugs, repo=repo, **vars(world))


def _block_echo(**changes):
    return {"enabled": True, "gate_mode": "block", "block_severity": "serious", **changes}


def test_webhook_gate_uses_the_settings_the_engine_read_at_run_start(workspace):
    result = workspace.finalize(a11y_settings=_block_echo(), a11y=_raw())

    report = workspace.store.get_by_execution(result.id)
    assert report.gate_status == GateStatus.BLOCKED and report.gate_mode == GateMode.BLOCK  # covers: AC-8
    snapshot = workspace.repo.get_execution_result(workspace.project.id, result.id).project_config_snapshot
    assert snapshot["target_url"] == TARGET and snapshot["framework"] == "playwright"  # covers: AC-13
    assert snapshot["a11y_gate_mode"] == "block" and snapshot["a11y_block_severity"] == "serious"
    assert result.status.value == "passed"
    assert len(workspace.bugs.calls) == 1  # covers: AC-6


def test_an_unknown_echoed_gate_mode_is_ignored_and_logged(workspace, caplog):
    with caplog.at_level(logging.WARNING, logger="backend.services.execution_service"):
        result = workspace.finalize(a11y_settings=_block_echo(gate_mode="strict"), a11y=_raw())

    assert workspace.store.get_by_execution(result.id).gate_mode == GateMode.WARN  # covers: AC-2, AC-8
    assert "a11y_settings field ignored: gate_mode" in caplog.text


def test_a_webhook_without_settings_or_results_is_a_scan_error(workspace, caplog):
    with caplog.at_level(logging.WARNING, logger="backend.services.execution_service"):
        result = workspace.finalize()

    report = workspace.store.get_by_execution(result.id)
    assert (report.gate_status, report.gate_reason) == (GateStatus.NOT_SCANNED, "scan_error")  # covers: AC-2, AC-8
    assert "no a11y_settings" in caplog.text


def test_an_engine_settings_read_failure_is_a_logged_scan_error(workspace, caplog):
    with caplog.at_level(logging.WARNING, logger="backend.services.execution_service"):
        result = workspace.finalize(a11y_settings_error="timeout")

    assert workspace.store.get_by_execution(result.id).gate_reason == "scan_error"  # covers: AC-2
    assert "a11y settings read failed in engine: timeout" in caplog.text


def test_a_repeated_webhook_keeps_one_report_and_the_first_snapshot(workspace):
    execution_id = uuid4()
    workspace.finalize(execution_id, a11y_settings=_block_echo(), a11y=_raw())

    workspace.project.a11y_enabled = False
    workspace.project.a11y_gate_mode = "off"
    workspace.repo.update_project(workspace.project)
    workspace.finalize(execution_id, a11y_settings=_block_echo(enabled=False, gate_mode="off"), a11y=_raw())

    reports, total = workspace.store.list_reports(workspace.project.id)
    assert total == 1  # covers: AC-1
    snapshot = workspace.repo.get_execution_result(workspace.project.id, execution_id).project_config_snapshot
    assert snapshot["a11y_gate_mode"] == "block"  # covers: AC-8
    assert reports[0].gate_mode == GateMode.BLOCK


def test_the_engine_decision_wins_over_a_later_global_switch(workspace, monkeypatch):
    from backend.config.settings import get_settings

    monkeypatch.setattr(get_settings().accessibility, "enabled", False)
    result = workspace.finalize(a11y_settings=_block_echo(), a11y=_raw())

    report = workspace.store.get_by_execution(result.id)
    assert report.gate_reason != "disabled" and report.gate_status == GateStatus.BLOCKED  # covers: AC-1, AC-8
    snapshot = workspace.repo.get_execution_result(workspace.project.id, result.id).project_config_snapshot
    assert snapshot["a11y_enabled"] == "true"


def test_a_project_without_a_target_url_still_finalizes(workspace):
    workspace.project.target_url = ""
    workspace.repo.update_project(workspace.project)

    result = workspace.finalize(a11y_settings=_block_echo(), a11y=_raw())

    snapshot = workspace.repo.get_execution_result(workspace.project.id, result.id).project_config_snapshot
    assert snapshot["target_url"] == "" and snapshot["a11y_gate_mode"] == "block"  # covers: AC-2, AC-8


def test_a_deleted_project_skips_accessibility_and_still_finalizes(workspace, caplog):
    workspace.repo.delete_project(workspace.project.id)

    with caplog.at_level(logging.WARNING, logger="backend.services.execution_service"):
        result = workspace.finalize(a11y_settings=_block_echo(), a11y=_raw())

    assert result.status.value == "passed"  # covers: AC-2
    assert workspace.store.list_reports(workspace.project.id)[1] == 0
    assert "accessibility processing skipped" in caplog.text


# ---------------------------------------------------------------- AC-14: report sections and JUnit


def _finding(rule_id, impact="serious", kind=FindingKind.VIOLATION, selector="#q", **extra):
    return AccessibilityFinding(signature=f"{rule_id}{selector}", kind=kind, rule_id=rule_id, impact=impact,
                                wcag_criteria=["4.1.2"], help=f"{rule_id} help", selector=selector,
                                pages=[f"{TARGET}/"], occurrences=1, ai_explanation=f"{rule_id} explained",
                                ai_fix="Add a label", **extra)


def _report(findings=(), gate=GateStatus.WARN, **extra):
    counts = {"critical": 0, "serious": 0, "moderate": 0, "minor": 0, "needs_review": 0}
    for f in findings:
        counts["needs_review" if f.kind == FindingKind.NEEDS_REVIEW else f.impact] += 1
    return AccessibilityReport(project_id=uuid4(), findings=list(findings), counts=counts, gate_status=gate, **extra)


def test_template_context_for_a_run_without_a_report_is_not_scanned():
    ctx = accessibility_template_context(None)
    assert (ctx["gate_status"], ctx["gate_reason"], ctx["scanned"]) == ("NOT_SCANNED", "no_report", False)  # covers: AC-14
    assert ctx["disclaimer"] == DISCLAIMER == "An automated scan does not certify WCAG conformance."


def test_template_context_explains_why_a_run_was_not_scanned():
    off = _report(gate=GateStatus.NOT_SCANNED, gate_reason="disabled")
    assert accessibility_template_context(off)["not_scanned_text"] == "Accessibility scanning is turned off for this project."


def test_template_context_caps_the_findings_table():
    findings = [_finding("label", selector=f"#f{n}") for n in range(MAX_TABLE_ROWS + 5)]
    ctx = accessibility_template_context(_report(findings))
    assert len(ctx["findings"]) == MAX_TABLE_ROWS and ctx["more_findings"] == 5
    assert ctx["counts"]["serious"] == MAX_TABLE_ROWS + 5


def test_legacy_html_section_shows_gate_counts_findings_and_disclaimer():
    html = accessibility_html_section(_report([_finding("label"), _finding("color-contrast", kind=FindingKind.NEEDS_REVIEW)]))
    assert "<h2>Accessibility</h2>" in html and ">WARN<" in html  # covers: AC-14
    assert "1 serious" in html and "1 need review" in html
    assert "<code>label</code>" in html and "Fix: Add a label" in html
    assert "<th scope='col'" in html and DISCLAIMER in html


def test_legacy_html_section_escapes_page_content():
    html = accessibility_html_section(_report([_finding("label", selector="<script>alert(1)</script>")]))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_legacy_html_section_for_a_report_without_findings():
    html = accessibility_html_section(_report(gate=GateStatus.PASS))
    assert "No accessibility findings on the scanned pages." in html and DISCLAIMER in html


def test_professional_template_renders_the_section():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(loader=FileSystemLoader(str(Path(__file__).parents[1] / "templates")),
                      autoescape=select_autoescape(["html"]))
    template = env.get_template("components/accessibility_section.html")
    scanned = template.render(context=SimpleNamespace(accessibility=accessibility_template_context(
        _report([_finding("label", selector="<b>x</b>")]))))
    unscanned = template.render(context=SimpleNamespace(accessibility=accessibility_template_context(None)))

    assert 'id="accessibility"' in scanned and "label" in scanned and DISCLAIMER in scanned  # covers: AC-14
    assert "<b>x</b>" not in scanned
    assert "This execution has no accessibility report." in unscanned and DISCLAIMER in unscanned


def test_legacy_pdf_page_carries_the_section_and_disclaimer():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    findings = [_finding("label", selector=f"#f{n}") for n in range(15)]
    draw_accessibility_pdf(lambda title: doc.new_page(), _report(findings, gate=GateStatus.BLOCKED))

    text = "".join(page.get_text() for page in doc)
    assert doc.page_count >= 2  # long finding lists flow onto more pages
    assert "GATE: BLOCKED" in text and "label" in text and DISCLAIMER in text  # covers: AC-14


def test_legacy_pdf_page_for_a_run_that_was_not_scanned():
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    draw_accessibility_pdf(lambda title: doc.new_page(), None)

    text = doc[0].get_text()
    assert "GATE: NOT_SCANNED" in text and "no accessibility report" in text and DISCLAIMER in text


def _junit(report):
    root = ET.Element("testsuites")
    return append_accessibility_junit(root, report)


def test_junit_fails_one_testcase_per_violating_rule():
    suite = _junit(_report([
        _finding("label", selector="#a"), _finding("label", selector="#b"),
        _finding("image-alt", impact="critical"),
        _finding("color-contrast", kind=FindingKind.NEEDS_REVIEW),
    ]))

    cases = {case.get("name"): case for case in suite.findall("testcase")}
    assert suite.get("name") == "accessibility" and sorted(cases) == ["image-alt", "label"]  # covers: AC-14
    assert (suite.get("tests"), suite.get("failures"), suite.get("skipped")) == ("2", "2", "0")
    failure = cases["label"].find("failure")
    assert failure.get("message").startswith("serious:") and "2 element(s)" in failure.text
    assert DISCLAIMER in failure.text


def test_junit_passes_a_scan_without_violations():
    suite = _junit(_report([_finding("color-contrast", kind=FindingKind.NEEDS_REVIEW)], gate=GateStatus.PASS))
    cases = suite.findall("testcase")
    assert [case.get("name") for case in cases] == ["no accessibility violations"]  # covers: AC-14
    assert cases[0].find("failure") is None and suite.get("failures") == "0"


@pytest.mark.parametrize("report, reason", [
    (None, "no_report"),
    (_report(gate=GateStatus.NOT_SCANNED, gate_reason="disabled"), "disabled"),
])
def test_junit_skips_a_run_that_was_not_scanned(report, reason):
    suite = _junit(report)
    skipped = suite.find("testcase/skipped")
    assert skipped.get("message") == f"Not scanned: {reason}"  # covers: AC-14
    assert (suite.get("tests"), suite.get("skipped")) == ("1", "1")


def test_report_lookup_errors_never_break_a_report():
    class Broken:
        def report_for_execution(self, project_id, execution_id):
            raise RuntimeError("store down")

    assert get_accessibility_report_by_execution(uuid4(), uuid4(), service=Broken()) is None


# ---------------------------------------------------------------- AC-5: shared redaction


@pytest.mark.parametrize("snippet", [
    f'<input value="{SECRET}">',
    f"<input value='{SECRET}'>",
    f"<input value={SECRET}>",
    f'<input VALUE = "{SECRET}">',
])
def test_clean_html_drops_every_value_attribute(snippet):
    assert clean_html(snippet, []) == "<input>"  # covers: AC-5


def test_clean_html_redacts_secrets_anywhere_and_caps_the_length():
    patterns = secret_patterns(uuid4(), vault=FakeVault())
    cleaned = clean_html(f'<div data-x="{SECRET.upper()}" title="qa-user">' + "x" * 600, patterns)
    assert SECRET.upper() not in cleaned and "qa-user" not in cleaned  # covers: AC-5
    assert len(cleaned) == 500


def test_redaction_still_covers_test_data_when_the_vault_fails(caplog):
    case = TestCase(scenario_id=uuid4(), title="Login", steps=["Log in"], expected_result="Home",
                    test_data={"password": "s3cret-value"})
    with caplog.at_level(logging.WARNING):
        patterns = secret_patterns(uuid4(), case, vault=BrokenVault())

    assert clean("typed s3cret-value", patterns) == "typed [redacted]"  # covers: AC-5
    assert "could not load vault credentials" in caplog.text


def test_clean_handles_empty_input():
    assert clean(None, []) == "" and clean_html(None, []) == ""


# ---------------------------------------------------------------- AC-3: per rule caps


def test_nodes_per_rule_and_pages_per_finding_are_capped(repo, world, store):
    service = _a11y(repo, store)
    config = service.config
    many_nodes = _rule("color-contrast", "serious", [f"#n{n}" for n in range(config.max_nodes_per_rule + 10)])
    pages = [f"{TARGET}/p{n}" for n in range(config.max_pages_per_finding + 5)]
    raw = _raw(violations=[])
    raw["results"] = [{"url": url, "violations": [_rule("label", "serious", ["#q"])], "incomplete": []} for url in pages]
    raw["results"][0]["incomplete"] = [many_nodes]

    report = service.process(world.project.id, None, raw, project_config={"target_url": TARGET}, enabled=True)

    review = [f for f in report.findings if f.kind == FindingKind.NEEDS_REVIEW]
    assert len(review) == config.max_nodes_per_rule  # covers: AC-3
    label = next(f for f in report.findings if f.rule_id == "label")
    assert len(label.pages) == config.max_pages_per_finding and label.occurrences == len(pages)
