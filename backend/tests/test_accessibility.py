"""Accessibility Agent, slices 1 and 2 (spec 0011): AccessibilityService, AccessibilityAgent,
stores, runner wiring, graph order, the execution and gate routes, project settings,
and AI guidance."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.agents.accessibility_agent import AccessibilityAgent
from backend.models.accessibility import (
    AccessibilityReport,
    AccessibilityReportStatus,
    AccessibilitySource,
    FindingKind,
    GateMode,
    GateStatus,
)
from backend.models.execution_result import ExecutionResult, ExecutionStatus
from backend.models.scenario import Scenario
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase
from backend.repository.accessibility_repository import (
    InMemoryAccessibilityReportRepository,
    JsonAccessibilityReportRepository,
)
from backend.repository.project_repository import JSONProjectRepository
from backend.services.accessibility_service import (
    AccessibilityService,
    finding_signature,
    normalize_selector,
    read_a11y_file,
    wcag_criteria,
)

SECRET = "hunter2-password"


class FakeVault:
    def get_credentials(self, project_id):
        return ["qa-user", SECRET]


def _rule(rule_id, impact, nodes, tags=("wcag2a", "wcag111")):
    return {
        "id": rule_id,
        "impact": impact,
        "tags": list(tags),
        "help": f"{rule_id} help",
        "helpUrl": f"https://dequeuniversity.com/rules/axe/4.14/{rule_id}",
        "description": f"{rule_id} description",
        "nodes": [
            {"target": target, "html": html, "failureSummary": f"Fix {rule_id}", "impact": impact}
            for target, html in nodes
        ],
        "nodes_total": len(nodes),
    }


def _raw(results=None, pages=None, status="succeeded"):
    results = results if results is not None else [
        {
            "url": "https://shop.example.test/",
            "violations": [
                _rule("image-alt", "critical", [(["img.hero"], '<img class="hero" src="a.png">')]),
                _rule("label", "serious", [(["#q"], f'<input id="q" value="{SECRET}" data-x="{SECRET}">')],
                      tags=("wcag2a", "wcag412", "wcag131")),
                _rule("region", "moderate", [(["div.promo"], "<div class=promo>")], tags=("best-practice",)),
            ],
            "incomplete": [_rule("color-contrast", "serious", [(["p.note"], "<p class=note>")], tags=("wcag2aa", "wcag143"))],
        },
        {
            "url": "https://shop.example.test/cart",
            "violations": [_rule("image-alt", "critical", [(["img.hero"], '<img class="hero" src="a.png">')])],
            "incomplete": [],
        },
    ]
    pages = pages if pages is not None else [
        {"url": "https://shop.example.test/", "title": "Home", "scanned_at": "2026-11-25T10:00:00Z", "error": None},
        {"url": "https://shop.example.test/cart", "title": "Cart", "scanned_at": "2026-11-25T10:00:02Z", "error": None},
        {"url": "https://shop.example.test/a", "title": "", "scanned_at": None, "error": "budget"},
    ]
    return {"status": status, "error": None, "wcag_tags": ["wcag2a"], "pages": pages, "results": results,
            "elapsed_ms": 1200, "truncated": False}


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
    requirement = repo.create_requirement(project.id, "Cart", "Shoppers use the cart", "high", "cart")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="Cart", description="Open the cart",
                        path_type="happy_path")
    repo.save_scenarios([scenario])
    case = TestCase(scenario_id=scenario.id, title="Open cart", steps=["Open the cart"], expected_result="Cart shows")
    repo.save_test_cases([case])
    return SimpleNamespace(project=project, case=case)


def _result(case, snapshot=None, **extra):
    return ExecutionResult(
        test_case_id=case.id,
        status=extra.pop("status", ExecutionStatus.PASSED),
        project_config_snapshot=snapshot if snapshot is not None else {"target_url": "https://shop.example.test"},
        **extra,
    )


def _service(repo, store=None):
    return AccessibilityService(repo=store or InMemoryAccessibilityReportRepository(), project_repo=repo, vault=FakeVault())


# ---------------------------------------------------------------- helpers


def test_wcag_criteria_and_selector_normalization():
    assert wcag_criteria(["wcag2a", "wcag143", "wcag1410", "best-practice", "wcag143"]) == ["1.4.3", "1.4.10"]
    assert normalize_selector([["iframe#pay", "button"]]) == "iframe#pay >> button"
    assert normalize_selector(["table > tbody > tr:nth-child(3) > td:nth-child(2) > a"]) == \
        "table > tbody > tr > td:nth-child(2) > a"
    assert normalize_selector(["ul > li:nth-child(7) > a"]) == "ul > li > a"
    assert finding_signature("label", "#q") == finding_signature("label", "#q") != finding_signature("label", "#r")


def test_read_a11y_file(tmp_path):
    assert read_a11y_file(tmp_path) is None
    (tmp_path / "a11y.json").write_text("{not json", encoding="utf-8")
    assert read_a11y_file(tmp_path) is None
    (tmp_path / "a11y.json").write_text('{"status": "succeeded", "pages": [], "results": []}', encoding="utf-8")
    assert read_a11y_file(tmp_path)["status"] == "succeeded"


# ---------------------------------------------------------------- processing


def test_process_builds_merged_redacted_findings_and_warns(repo, world):
    service = _service(repo)
    result = _result(world.case, batch_id=uuid4())
    report = service.process(world.project.id, result, _raw())

    assert report.status == AccessibilityReportStatus.SUCCEEDED
    assert report.execution_id == result.id and report.test_case_id == world.case.id
    assert report.batch_id == str(result.batch_id)
    assert [p.error for p in report.pages] == [None, None, "budget"]

    # AC-3: the same rule and element on two pages is one finding.
    image = next(f for f in report.findings if f.rule_id == "image-alt")
    assert image.occurrences == 2 and image.pages == ["https://shop.example.test/", "https://shop.example.test/cart"]
    assert image.wcag_criteria == ["1.1.1"] and image.kind == FindingKind.VIOLATION
    review = next(f for f in report.findings if f.kind == FindingKind.NEEDS_REVIEW)
    assert review.rule_id == "color-contrast" and review.wcag_criteria == ["1.4.3"]
    # Ordered by impact, needs review last.
    assert [f.rule_id for f in report.findings] == ["image-alt", "label", "region", "color-contrast"]
    assert report.counts == {"critical": 1, "serious": 1, "moderate": 1, "minor": 0, "needs_review": 1}

    # AC-5: no vault value and no value attribute is kept.
    label = next(f for f in report.findings if f.rule_id == "label")
    assert SECRET not in label.html_snippet and "value=" not in label.html_snippet
    assert "[redacted]" in label.html_snippet
    assert SECRET not in report.model_dump_json()

    # AC-4 fallback text until enrichment lands.
    assert label.ai_source == "axe_fallback" and label.ai_explanation == "label help" and label.ai_fix == "Fix label"

    # AC-8: warn mode by default, a critical violation warns.
    assert report.gate_mode == GateMode.WARN and report.gate_status == GateStatus.WARN
    assert service.repo.get_by_execution(result.id).id == report.id


def test_snippets_are_capped(repo, world):
    long_html = "<div>" + "x" * 2000 + "</div>"
    raw = _raw(results=[{"url": "https://shop.example.test/", "incomplete": [],
                         "violations": [_rule("region", "moderate", [(["div"], long_html)])]}])
    report = _service(repo).process(world.project.id, _result(world.case), raw)
    assert len(report.findings[0].html_snippet) == 500


@pytest.mark.parametrize("mode,severity,expected", [
    ("off", "serious", GateStatus.PASS),
    ("warn", "serious", GateStatus.WARN),
    ("block", "serious", GateStatus.BLOCKED),
    ("block", "critical", GateStatus.PASS),
])
def test_gate_uses_the_run_snapshot(repo, world, mode, severity, expected):
    # Only a serious violation and a serious needs review finding.
    raw = _raw(results=[{
        "url": "https://shop.example.test/",
        "violations": [_rule("label", "serious", [(["#q"], "<input id=q>")])],
        "incomplete": [_rule("color-contrast", "critical", [(["p"], "<p>")])],
    }])
    snapshot = {"a11y_gate_mode": mode, "a11y_block_severity": severity}
    report = _service(repo).process(world.project.id, _result(world.case, snapshot), raw)
    assert report.gate_status == expected and report.gate_mode == GateMode(mode)


def test_moderate_minor_and_needs_review_never_affect_the_gate(repo, world):
    raw = _raw(results=[{
        "url": "https://shop.example.test/",
        "violations": [_rule("region", "moderate", [(["div"], "<div>")]), _rule("x", "minor", [(["p"], "<p>")])],
        "incomplete": [_rule("color-contrast", "critical", [(["p"], "<p>")])],
    }])
    snapshot = {"a11y_gate_mode": "block"}
    assert _service(repo).process(world.project.id, _result(world.case, snapshot), raw).gate_status == GateStatus.PASS


def test_missing_raw_data_is_a_scan_error_and_never_raises(repo, world):
    service = _service(repo)
    result = _result(world.case, status=ExecutionStatus.PASSED)
    report = service.process(world.project.id, result, None)
    assert report.status == AccessibilityReportStatus.FAILED and report.error == "scan_error"
    assert report.gate_status == GateStatus.NOT_SCANNED and report.gate_reason == "scan_error"
    assert result.status == ExecutionStatus.PASSED  # AC-2: the run is untouched.

    failed = service.process(world.project.id, _result(world.case), _raw(status="failed"))
    assert failed.gate_status == GateStatus.NOT_SCANNED and failed.gate_reason == "scan_error"


def test_disabled_scanning_is_not_scanned(repo, world, monkeypatch):
    service = _service(repo)
    off_project = service.process(world.project.id, _result(world.case, {"a11y_enabled": "false"}), _raw())
    assert off_project.gate_status == GateStatus.NOT_SCANNED and off_project.gate_reason == "disabled"

    from backend.config.settings import get_settings

    settings = get_settings().model_copy(deep=True)  # get_settings() is cached; never mutate it.
    settings.accessibility.enabled = False
    off_global = AccessibilityService(repo=InMemoryAccessibilityReportRepository(), project_repo=repo,
                                      vault=FakeVault(), settings=settings)
    assert off_global.process(world.project.id, _result(world.case), _raw()).gate_reason == "disabled"


def test_processing_errors_are_saved_as_failed(repo, world):
    raw = _raw()
    # Tags that are not a list make the normalizer raise.
    raw["results"] = [{"url": "https://shop.example.test/",
                       "violations": [{"id": "x", "tags": 5, "nodes": [{"target": ["a"]}]}]}]
    report = _service(repo).process(world.project.id, _result(world.case), raw)
    assert report.status == AccessibilityReportStatus.FAILED and report.gate_status == GateStatus.NOT_SCANNED


def test_a_store_failure_never_raises(repo, world):
    class BrokenStore(InMemoryAccessibilityReportRepository):
        def save(self, report):
            raise RuntimeError("db down")

    report = _service(repo, BrokenStore()).process(world.project.id, _result(world.case), _raw())
    assert report.gate_status == GateStatus.WARN


def test_upsert_on_execution_replaces_the_report(repo, world):
    service = _service(repo)
    result = _result(world.case)
    first = service.process(world.project.id, result, None)
    second = service.process(world.project.id, result, _raw())
    assert second.id == first.id and second.created_at == first.created_at
    reports, total = service.repo.list_reports(world.project.id)
    assert total == 1 and reports[0].gate_status == GateStatus.WARN


def test_relink_execution_moves_the_report(repo, world):
    service = _service(repo)
    result = _result(world.case)
    service.process(world.project.id, result, _raw())
    final_id = uuid4()
    service.relink_execution(result.id, final_id)
    assert service.repo.get_by_execution(result.id) is None
    assert service.repo.get_by_execution(final_id) is not None


def test_batch_gate_is_the_worst_run(repo, world):
    service = _service(repo)
    batch = uuid4()
    blocked = _result(world.case, {"a11y_gate_mode": "block"}, batch_id=batch)
    clean_run = _result(world.case, {"a11y_gate_mode": "block"}, batch_id=batch)
    unscanned = uuid4()
    service.process(world.project.id, blocked, _raw())
    service.process(world.project.id, clean_run, _raw(results=[]))
    gate = service.gate_for_batch(world.project.id, str(batch), [blocked.id, clean_run.id, unscanned])
    assert gate["gate_status"] == "BLOCKED" and gate["gate_mode"] == "block"
    assert {r["execution_id"]: r["gate_status"] for r in gate["runs"]} == {
        str(blocked.id): "BLOCKED", str(clean_run.id): "PASS", str(unscanned): "NOT_SCANNED",
    }
    assert service.gate_for_batch(world.project.id, "nope", [unscanned])["gate_status"] == "NOT_SCANNED"


def test_batch_gate_with_reports_but_no_saved_runs_is_a_normal_gate(repo, world):
    # covers: spec 0012, AC-6, AC-12. Reports alone make it a normal gate, not an
    # empty batch answer, whatever the log says.
    service = _service(repo)
    batch = uuid4()
    blocked = _result(world.case, {"a11y_gate_mode": "block"}, batch_id=batch)
    service.process(world.project.id, blocked, _raw())

    gate = service.gate_for_batch(world.project.id, str(batch), [], "running")

    assert gate["gate_status"] == "BLOCKED" and gate["gate_reason"] is None
    assert gate["runs"] == [{"execution_id": str(blocked.id), "gate_status": "BLOCKED"}]
    assert service.gate_for_batch(world.project.id, str(uuid4()), [], None) is None


@pytest.mark.parametrize(
    "modes_and_results, expected",
    [
        # covers: AC-9, worst first is BLOCKED, then WARN, then NOT_SCANNED, then PASS.
        ([("warn", "findings"), ("warn", "clean")], "WARN"),
        ([("block", "findings"), ("warn", "findings")], "BLOCKED"),
        ([("warn", "clean"), ("warn", "clean")], "PASS"),
    ],
)
def test_batch_gate_returns_the_worst_status_among_scanned_runs(repo, world, modes_and_results, expected):
    service = _service(repo)
    batch = uuid4()
    ids = []
    for mode, kind in modes_and_results:
        result = _result(world.case, {"a11y_gate_mode": mode}, batch_id=batch)
        service.process(world.project.id, result, _raw() if kind == "findings" else _raw(results=[]))
        ids.append(result.id)

    gate = service.gate_for_batch(world.project.id, str(batch), ids)

    assert gate["gate_status"] == expected


def test_batch_gate_with_a_passing_and_an_unscanned_run_is_not_scanned(repo, world):
    # covers: AC-9, a run with no report counts as NOT_SCANNED, which outranks PASS.
    service = _service(repo)
    batch = uuid4()
    clean_run = _result(world.case, batch_id=batch)
    service.process(world.project.id, clean_run, _raw(results=[]))

    gate = service.gate_for_batch(world.project.id, str(batch), [clean_run.id, uuid4()])

    assert gate["gate_status"] == "NOT_SCANNED"
    assert gate["gate_reason"] == "no_report"


def test_batch_gate_takes_the_reason_of_the_worst_report(repo, world):
    service = _service(repo)
    batch = uuid4()
    disabled = _result(world.case, batch_id=batch)
    clean_run = _result(world.case, batch_id=batch)
    service.process(world.project.id, disabled, _raw(), enabled=False)
    service.process(world.project.id, clean_run, _raw(results=[]))

    gate = service.gate_for_batch(world.project.id, str(batch), [disabled.id, clean_run.id])

    assert gate["gate_status"] == "NOT_SCANNED"
    assert gate["gate_reason"] == "disabled"


def test_batch_gate_mode_is_mixed_when_runs_disagree(repo, world):
    service = _service(repo)
    batch = uuid4()
    ids = []
    for mode in ("warn", "block"):
        result = _result(world.case, {"a11y_gate_mode": mode}, batch_id=batch)
        service.process(world.project.id, result, _raw(results=[]))
        ids.append(result.id)

    assert service.gate_for_batch(world.project.id, str(batch), ids)["gate_mode"] == "mixed"


# ---------------------------------------------------------------- agent and graph


def test_agent_writes_one_report_per_result(repo, world):
    service = _service(repo)
    results = [_result(world.case), _result(world.case)]
    raws = {results[0].id: _raw(), results[1].id: None}
    agent = AccessibilityAgent(accessibility_service=service, raw_reader=lambda r: raws[r.id])
    state = WorkflowState(project_id=world.project.id, execution_results=results)

    state = agent.run(state)
    assert [r.gate_status for r in state.accessibility_results] == [GateStatus.WARN, GateStatus.NOT_SCANNED]
    assert any("Accessibility Agent completed" in line for line in state.logs)
    # Running again does not duplicate reports.
    assert len(agent.run(state).accessibility_results) == 2


def test_agent_skips_without_results_or_a_project(repo, world):
    agent = AccessibilityAgent(accessibility_service=_service(repo), raw_reader=lambda r: None)
    assert agent.run(WorkflowState()).accessibility_results == []
    no_project = agent.run(WorkflowState(execution_results=[_result(world.case)]))
    assert no_project.accessibility_results == [] and "skipped" in no_project.logs[-1]


def test_graph_runs_accessibility_between_analysis_and_bug_reporting():
    from backend.graph.workflow import build_graph

    graph = build_graph().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert ("execution_analysis_agent", "accessibility_agent") in edges
    assert ("accessibility_agent", "bug_reporting_agent") in edges
    assert ("execution_analysis_agent", "bug_reporting_agent") not in edges


def test_execution_agent_turns_the_runner_scan_on(world):
    from backend.agents.execution_agent import ExecutionAgent

    class Runner:
        a11y_scan = None

        def run(self, script, run_id, **kwargs):
            from backend.services.playwright_runner import PlaywrightRunResult

            Runner.seen = self.a11y_scan
            return PlaywrightRunResult(status="passed")

    world.case.playwright_script = "test('x', async () => {});"
    from backend.models.requirement import Requirement

    state = WorkflowState(
        project_id=world.project.id,
        requirement=Requirement(project_id=world.project.id, title="r", description="d", priority="high", module="m"),
        generated_test_cases=[world.case], human_approved_test_case_ids=[world.case.id],
    )
    ExecutionAgent(runner=Runner(), project_config={"a11y_enabled": "true"}).run(state)
    assert Runner.seen is True
    ExecutionAgent(runner=Runner(), project_config={"a11y_enabled": "false"}).run(state)
    assert Runner.seen is False


# ---------------------------------------------------------------- runner wiring


def test_runner_only_scans_when_asked(monkeypatch):
    from backend.services.playwright_runner import PlaywrightRunner

    assert PlaywrightRunner().a11y_scan is False
    env = PlaywrightRunner._a11y_env()
    assert env["A11Y_SCAN"] == "1" and env["A11Y_TAGS"] == "wcag2a,wcag2aa,wcag21a,wcag21aa,wcag22aa"
    assert env["A11Y_MAX_PAGES"] == "10" and env["A11Y_PAGE_BUDGET_MS"] == "30000"


def test_unknown_wcag_tags_fail_when_the_config_loads():
    from pydantic import ValidationError

    from backend.config.settings import AccessibilityConfig

    with pytest.raises(ValidationError):
        AccessibilityConfig(wcag_tags=["wcag2a", "wcag99zz"])


# ---------------------------------------------------------------- stores


def test_json_store_round_trips_and_deletes_by_project(tmp_path):
    store = JsonAccessibilityReportRepository(tmp_path / "a11y.json")
    project_id, other = uuid4(), uuid4()
    execution_id = uuid4()
    report = AccessibilityReport(project_id=project_id, execution_id=execution_id, batch_id="b1",
                                 gate_status=GateStatus.WARN)
    store.save(report)
    store.save(AccessibilityReport(project_id=other, source=AccessibilitySource.SCAN))

    assert store.get(report.id).gate_status == GateStatus.WARN
    assert store.get_by_execution(execution_id).id == report.id
    assert [r.id for r in store.list_by_batch(project_id, "b1")] == [report.id]
    rows, total = store.list_reports(project_id, source="execution")
    assert total == 1 and rows[0].id == report.id
    with pytest.raises(ValueError):
        store.save(AccessibilityReport(project_id=project_id, execution_id=execution_id))
    assert store.delete_for_project(project_id) == 1
    assert store.get(report.id) is None and store.list_reports(other)[1] == 1


def test_json_store_never_overwrites_a_file_it_cannot_decode(tmp_path):
    """Review 2026-11-27: a damaged store must raise, not read as empty, or the
    next save replaces every report with one."""
    path = tmp_path / "a11y.json"
    store = JsonAccessibilityReportRepository(path)
    store.save(AccessibilityReport(project_id=uuid4()))
    damaged = path.read_text(encoding="utf-8")[:-10]
    path.write_text(damaged, encoding="utf-8")

    with pytest.raises(ValueError):
        store.save(AccessibilityReport(project_id=uuid4()))
    assert path.read_text(encoding="utf-8") == damaged


def test_json_store_treats_an_empty_file_as_empty(tmp_path):
    path = tmp_path / "a11y.json"
    path.write_text("", encoding="utf-8")
    store = JsonAccessibilityReportRepository(path)
    report = store.save(AccessibilityReport(project_id=uuid4()))
    assert store.get(report.id) is not None


def test_postgres_store_round_trips(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import backend.repository.postgres_accessibility_repository as module
    from backend.database.db import Base
    from backend.database.db_models import ProjectDB

    engine = create_engine(f"sqlite:///{tmp_path / 'a11y.db'}")
    tables = [Base.metadata.tables[name] for name in ("users", "projects", "releases", "accessibility_reports")]
    saved = {t.name: set(t.indexes) for t in tables if t.name != "accessibility_reports"}
    for table in tables:
        if table.name != "accessibility_reports":
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

    store = module.PostgresAccessibilityReportRepository()
    execution_id = uuid4()
    raw_service = AccessibilityService(repo=store, project_repo=SimpleNamespace(get_test_case=lambda _id: None),
                                       vault=FakeVault())
    result = ExecutionResult(id=execution_id, test_case_id=uuid4(), status=ExecutionStatus.PASSED,
                             batch_id=uuid4())
    report = raw_service.process(project_id, result, _raw())
    again = raw_service.process(project_id, result, _raw())

    loaded = store.get_by_execution(execution_id)
    assert again.id == report.id and loaded.gate_status == GateStatus.WARN
    assert loaded.findings[0].rule_id == "image-alt" and loaded.counts["critical"] == 1
    assert [r.id for r in store.list_by_batch(project_id, str(result.batch_id))] == [report.id]
    rows, total = store.list_reports(project_id, limit=5)
    assert total == 1 and rows[0].id == report.id
    assert store.delete_for_project(project_id) == 1 and store.get(report.id) is None
    engine.dispose()


def test_project_delete_removes_its_reports(repo, world, offline_accessibility_reports):
    from backend.services.project_service import ProjectService

    offline_accessibility_reports.save(AccessibilityReport(project_id=world.project.id))
    # The repo fixture is the active project repository.
    assert ProjectService().delete_project(world.project.id)
    assert offline_accessibility_reports.list_reports(world.project.id)[1] == 0


# ---------------------------------------------------------------- routes


@pytest.fixture
def api(repo, world):
    from backend.main import app, get_accessibility_service

    service = _service(repo)
    app.dependency_overrides[get_accessibility_service] = lambda: service
    yield SimpleNamespace(client=TestClient(app), service=service, **vars(world))
    app.dependency_overrides.pop(get_accessibility_service, None)


def test_execution_route_returns_the_report_or_not_scanned(api, repo):
    base = f"/api/v1/projects/{api.project.id}"
    scanned, unscanned = _result(api.case), _result(api.case)
    repo.save_execution_result(api.project.id, scanned)
    repo.save_execution_result(api.project.id, unscanned)
    api.service.process(api.project.id, scanned, _raw())

    body = api.client.get(f"{base}/executions/{scanned.id}/accessibility").json()
    assert body["gate_status"] == "WARN" and len(body["findings"]) == 4
    assert api.client.get(f"{base}/executions/{unscanned.id}/accessibility").json() == {
        "gate_status": "NOT_SCANNED", "gate_reason": "no_report",
    }
    assert api.client.get(f"{base}/executions/{uuid4()}/accessibility").status_code == 404


def test_reports_from_another_project_are_404(api, repo):
    other = repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    result = _result(api.case)
    repo.save_execution_result(api.project.id, result)
    api.service.process(api.project.id, result, _raw())

    assert api.client.get(f"/api/v1/projects/{other.id}/executions/{result.id}/accessibility").status_code == 404
    gate = api.client.get(f"/api/v1/projects/{other.id}/accessibility/gate", params={"execution_id": str(result.id)})
    assert gate.status_code == 404


def test_gate_route(api, repo, webhook_headers):
    url = f"/api/v1/projects/{api.project.id}/accessibility/gate"
    batch = uuid4()
    result = _result(api.case, {"a11y_gate_mode": "block"}, batch_id=batch)
    repo.save_execution_result(api.project.id, result)
    api.service.process(api.project.id, result, _raw())

    assert api.client.get(url).status_code == 422
    assert api.client.get(url, params={"execution_id": str(result.id), "batch_id": "x"}).status_code == 422

    by_run = api.client.get(url, params={"execution_id": str(result.id)}, headers=webhook_headers())
    assert by_run.status_code == 200
    assert by_run.json() == {
        "gate_status": "BLOCKED", "gate_reason": None, "gate_mode": "block",
        "runs": [{"execution_id": str(result.id), "gate_status": "BLOCKED"}],
        "in_progress": False,
    }
    by_batch = api.client.get(url, params={"batch_id": str(batch)}, headers=webhook_headers())
    assert by_batch.json()["gate_status"] == "BLOCKED"
    assert api.client.get(url, params={"batch_id": "missing"}).status_code == 404
    bad_secret = api.client.get(url, params={"execution_id": str(result.id)}, headers={"X-Webhook-Secret": "wrong"})
    assert bad_secret.status_code == 401


def test_batch_gate_route_finds_a_batch_run_with_no_cycle(api, repo, webhook_headers):
    # covers: spec 0012, AC-6. A batch run has a batch_id and no test cycle,
    # and the gate finds it by that batch_id.
    url = f"/api/v1/projects/{api.project.id}/accessibility/gate"
    batch = uuid4()
    result = _result(api.case, {"a11y_gate_mode": "block"}, batch_id=batch)
    repo.save_execution_result(api.project.id, result)
    api.service.process(api.project.id, result, _raw())

    response = api.client.get(url, params={"batch_id": str(batch)}, headers=webhook_headers())

    assert response.status_code == 200
    assert response.json()["gate_status"] == "BLOCKED"
    assert response.json()["runs"] == [{"execution_id": str(result.id), "gate_status": "BLOCKED"}]


def test_batch_gate_route_lists_a_batch_run_with_no_report(api, repo, webhook_headers):
    # covers: AC-9, the route lists every run of the batch, scanned or not.
    url = f"/api/v1/projects/{api.project.id}/accessibility/gate"
    batch = uuid4()
    scanned = _result(api.case, batch_id=batch)
    unscanned = _result(api.case, batch_id=batch)
    repo.save_execution_result(api.project.id, scanned)
    repo.save_execution_result(api.project.id, unscanned)
    api.service.process(api.project.id, scanned, _raw(results=[]))

    body = api.client.get(url, params={"batch_id": str(batch)}, headers=webhook_headers()).json()

    assert body["gate_status"] == "NOT_SCANNED"
    assert {r["execution_id"]: r["gate_status"] for r in body["runs"]} == {
        str(scanned.id): "PASS", str(unscanned.id): "NOT_SCANNED",
    }


def test_batch_gate_route_is_404_for_another_projects_batch(api, repo, webhook_headers):
    # covers: AC-9 and AC-15, a batch id from one project reveals nothing in another.
    other = repo.create_project("Other", "", framework="playwright", target_url="https://other.example.test")
    batch = uuid4()
    result = _result(api.case, {"a11y_gate_mode": "block"}, batch_id=batch)
    repo.save_execution_result(api.project.id, result)
    api.service.process(api.project.id, result, _raw())

    response = api.client.get(
        f"/api/v1/projects/{other.id}/accessibility/gate", params={"batch_id": str(batch)}, headers=webhook_headers(),
    )

    assert response.status_code == 404


def test_route_permissions(api, repo, sign_in):
    base = f"/api/v1/projects/{api.project.id}"
    result = _result(api.case)
    repo.save_execution_result(api.project.id, result)
    viewer = sign_in("Stakeholder", projects=[api.project.id]).headers
    outsider = sign_in("Tester").headers

    assert api.client.get(f"{base}/executions/{result.id}/accessibility", headers=viewer).status_code == 200
    assert api.client.get(f"{base}/executions/{result.id}/accessibility", headers=outsider).status_code == 403
    gate = api.client.get(f"{base}/accessibility/gate", params={"execution_id": str(result.id)}, headers=outsider)
    assert gate.status_code == 403


# ---------------------------------------------------------------- slice 2: project settings (AC-8, AC-13)


def _settings_body(**extra):
    return {"name": "Shop", "description": "", "line_of_business": "general", "framework": "playwright",
            "target_url": "https://shop.example.test", **extra}


def test_project_accessibility_settings_defaults_save_and_validation(api, repo):
    url = f"/api/v1/projects/{api.project.id}"
    projects = api.client.get("/api/v1/projects").json()
    mine = next(p for p in projects if p["id"] == str(api.project.id))
    assert (mine["a11y_enabled"], mine["a11y_gate_mode"], mine["a11y_block_severity"]) == (True, "warn", "serious")

    saved = api.client.put(url, json=_settings_body(a11y_enabled=False, a11y_gate_mode="block",
                                                    a11y_block_severity="critical"))
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert (body["a11y_enabled"], body["a11y_gate_mode"], body["a11y_block_severity"]) == (False, "block", "critical")
    stored = repo.get_project(api.project.id)
    assert (stored.a11y_enabled, stored.a11y_gate_mode, stored.a11y_block_severity) == (False, "block", "critical")

    # Literal typed: any other value is a 422 and changes nothing.
    assert api.client.put(url, json=_settings_body(a11y_gate_mode="strict")).status_code == 422
    assert api.client.put(url, json=_settings_body(a11y_block_severity="moderate")).status_code == 422
    # A null (or a missing field) in a partial update leaves the value unchanged.
    kept = api.client.put(url, json=_settings_body(a11y_enabled=None, a11y_gate_mode=None))
    assert kept.status_code == 200
    stored = repo.get_project(api.project.id)
    assert (stored.a11y_enabled, stored.a11y_gate_mode, stored.a11y_block_severity) == (False, "block", "critical")


def test_snapshot_copies_the_settings_and_the_gate_reads_the_snapshot(repo, world):
    from backend.services.project_configuration import capture_project_snapshot

    world.project.a11y_gate_mode = "block"
    world.project.a11y_block_severity = "critical"
    snapshot = capture_project_snapshot(world.project)
    assert snapshot["a11y_enabled"] == "true"
    assert (snapshot["a11y_gate_mode"], snapshot["a11y_block_severity"]) == ("block", "critical")
    # Every value stays a string, so the ExecutionResult snapshot type holds.
    assert all(isinstance(v, str) for v in snapshot.values())

    service = _service(repo)
    report = service.process(world.project.id, _result(world.case, snapshot=snapshot), _raw())
    assert report.gate_mode == GateMode.BLOCK and report.gate_status == GateStatus.BLOCKED

    # A later settings change never rewrites the run's gate.
    world.project.a11y_gate_mode = "off"
    repo.update_project(world.project)
    again = service.process(world.project.id, _result(world.case, snapshot=snapshot), _raw())
    assert again.gate_status == GateStatus.BLOCKED

    world.project.a11y_enabled = False
    off = capture_project_snapshot(world.project)
    assert off["a11y_enabled"] == "false" and service.scan_enabled(off) is False
    assert service.process(world.project.id, _result(world.case, snapshot=off), _raw()).gate_reason == "disabled"


def test_postgres_project_mapping_defaults_unknown_accessibility_values():
    from backend.repository.postgres_project_repository import _a11y_fields

    assert _a11y_fields(SimpleNamespace()) == {
        "a11y_enabled": True, "a11y_gate_mode": "warn", "a11y_block_severity": "serious"}
    row = SimpleNamespace(a11y_enabled=False, a11y_gate_mode="block", a11y_block_severity="critical")
    assert _a11y_fields(row) == {
        "a11y_enabled": False, "a11y_gate_mode": "block", "a11y_block_severity": "critical"}
    assert _a11y_fields(SimpleNamespace(a11y_gate_mode="strict"))["a11y_gate_mode"] == "warn"


# ---------------------------------------------------------------- slice 2: AI guidance (AC-4, AC-5)


class RecordingLLM:
    def __init__(self, respond=None, error=None):
        self.calls = []
        self.respond = respond
        self.error = error

    def structured_generate(self, *, user, response_model, **kwargs):
        import json

        self.calls.append({"user": user, "response_model": response_model, **kwargs})
        if self.error:
            raise self.error
        findings = json.loads(user.split("<findings>", 1)[1].split("</findings>", 1)[0])
        items = self.respond(findings) if self.respond else [
            {"id": f["id"], "explanation": f"AI says {f['rule_id']}", "fix": f"AI fix {f['rule_id']}",
             "review_guidance": f"Check {f['rule_id']}" if f["kind"] == "needs_review" else ""}
            for f in findings
        ]
        return response_model.model_validate({"items": items})


def _enrich_service(repo, llm, max_llm_findings=None):
    from backend.config.settings import get_settings

    settings = get_settings().model_copy(deep=True)  # get_settings() is cached; never mutate it.
    if max_llm_findings is not None:
        settings.accessibility.max_llm_findings = max_llm_findings
    return AccessibilityService(repo=InMemoryAccessibilityReportRepository(), project_repo=repo,
                                vault=FakeVault(), settings=settings, llm_service=llm)


def test_enrichment_explains_the_top_findings_in_one_call(repo, world):
    llm = RecordingLLM()
    report = _enrich_service(repo, llm, max_llm_findings=2).process(world.project.id, _result(world.case), _raw())

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["response_model"].__name__ == "AccessibilityEnrichment"
    assert call["max_retries"] == 1 and call["timeout"] == 60
    # Highest impact first: only the critical and the serious violation fit the cap.
    by_rule = {f.rule_id: f for f in report.findings}
    assert by_rule["image-alt"].ai_source == "llm" and by_rule["image-alt"].ai_explanation == "AI says image-alt"
    assert by_rule["label"].ai_fix == "AI fix label"
    assert by_rule["region"].ai_source == "axe_fallback" and by_rule["region"].ai_explanation == "region help"
    assert by_rule["color-contrast"].ai_source == "axe_fallback"
    assert report.status == AccessibilityReportStatus.SUCCEEDED


def test_needs_review_findings_get_what_to_check(repo, world):
    report = _enrich_service(repo, RecordingLLM()).process(world.project.id, _result(world.case), _raw())
    review = next(f for f in report.findings if f.kind == FindingKind.NEEDS_REVIEW)
    assert review.ai_source == "llm"
    assert review.ai_fix == "Check color-contrast\n\nIf it fails: AI fix color-contrast"


def test_enrichment_sends_and_keeps_only_redacted_text(repo, world):
    echo = RecordingLLM(respond=lambda findings: [
        {"id": f["id"], "explanation": f"Leaked {SECRET}", "fix": f"value={SECRET}"} for f in findings])
    report = _enrich_service(repo, echo).process(world.project.id, _result(world.case), _raw())

    prompt = echo.calls[0]["user"]
    assert SECRET not in prompt and 'value=\\"' not in prompt
    assert SECRET not in report.model_dump_json()


def test_llm_failure_falls_back_to_axe_text_and_never_fails_the_run(repo, world):
    llm = RecordingLLM(error=TimeoutError("slow provider"))
    report = _enrich_service(repo, llm).process(world.project.id, _result(world.case), _raw())

    assert len(llm.calls) == 1
    assert report.status == AccessibilityReportStatus.SUCCEEDED and report.gate_status == GateStatus.WARN
    assert all(f.ai_source == "axe_fallback" for f in report.findings)


def test_unknown_ids_and_empty_answers_keep_the_fallback(repo, world):
    llm = RecordingLLM(respond=lambda findings: [
        {"id": 99, "explanation": "no such finding"}, {"id": 0, "explanation": "   "}])
    report = _enrich_service(repo, llm).process(world.project.id, _result(world.case), _raw())
    assert all(f.ai_source == "axe_fallback" for f in report.findings)


def test_no_llm_call_without_findings_or_with_a_zero_cap(repo, world):
    llm = RecordingLLM()
    _enrich_service(repo, llm).process(world.project.id, _result(world.case), _raw(results=[]))
    _enrich_service(repo, llm, max_llm_findings=0).process(world.project.id, _result(world.case), _raw())
    assert llm.calls == []


def test_enrichment_tokens_are_attributed_to_the_accessibility_agent():
    from backend.services.accessibility_service import LLM_AGENT_NAME
    from backend.services.token_usage_service import stage_for

    assert LLM_AGENT_NAME == "accessibility_agent" and stage_for(LLM_AGENT_NAME) == "Analysis"


class _TaskCollector:
    def __init__(self):
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))

    def run(self):
        for func, args, kwargs in self.tasks:
            func(*args, **kwargs)


class _RecordingBugs:
    def __init__(self):
        self.drafted = []

    def draft_from_accessibility(self, project_id, report, result, test_case):
        self.drafted.append([f.ai_source for f in report.findings])


def _workspace_webhook(monkeypatch, repo, world, llm, background_tasks):
    import backend.services.execution_service as execution_service
    from backend.services.execution_service import ExecutionService

    monkeypatch.setattr(execution_service, "emit_execution_completed", lambda *args: None)
    monkeypatch.setattr(ExecutionService, "apply_object_repository_markers", lambda *args: 0)
    a11y = _enrich_service(repo, llm)
    store = a11y.repo
    bugs = _RecordingBugs()
    service = ExecutionService(bug_report_service=bugs, accessibility_service=a11y)
    exec_id = uuid4()
    service.finalize_execution(
        world.project.id,
        {
            "execution_id": str(exec_id),
            "test_case_id": str(world.case.id),
            "status": "passed",
            "a11y_settings": {"enabled": True, "gate_mode": "warn", "block_severity": "serious"},
            "a11y": _raw(),
        },
        background_tasks=background_tasks,
    )
    return store, bugs, exec_id


def test_workspace_webhook_defers_enrichment_and_bugs_to_background(monkeypatch, repo, world):
    """Review 2026-11-27 (major): the webhook must not wait on the LLM. The
    report is saved and gated with axe text first; enrichment and bug drafts
    run after the response."""
    llm = RecordingLLM()
    tasks = _TaskCollector()
    store, bugs, exec_id = _workspace_webhook(monkeypatch, repo, world, llm, tasks)

    saved = store.get_by_execution(exec_id)
    assert llm.calls == [] and bugs.drafted == []
    assert saved.gate_status == GateStatus.WARN
    assert all(f.ai_source != "llm" for f in saved.findings)

    tasks.run()

    assert len(llm.calls) == 1
    enriched = store.get_by_execution(exec_id)
    assert enriched.gate_status == GateStatus.WARN
    assert any(f.ai_source == "llm" for f in enriched.findings)
    assert len(bugs.drafted) == 1 and "llm" in bugs.drafted[0]


def test_workspace_webhook_without_background_tasks_still_enriches_inline(monkeypatch, repo, world):
    llm = RecordingLLM()
    store, bugs, exec_id = _workspace_webhook(monkeypatch, repo, world, llm, None)

    assert len(llm.calls) == 1 and len(bugs.drafted) == 1
    assert any(f.ai_source == "llm" for f in store.get_by_execution(exec_id).findings)
