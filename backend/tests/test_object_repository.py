"""Object repository (spec 0004): service rules, scripts, run feedback, recon merge, API."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.scenario import Scenario
from backend.models.test_case import EvaluationStatus, TestCase
from backend.repository.project_repository import JSONProjectRepository
from backend.services import object_repository_script as scripts
from backend.services.object_repository_service import (
    ObjectRepositoryConflict,
    ObjectRepositoryInvalid,
    ObjectRepositoryService,
    matching_pages,
    page_pattern_for_path,
    pattern_matches,
)

SCRIPT = """import { test, expect } from '@playwright/test';

test('Login works', async ({ page }) => {
  await page.goto(process.env.TARGET_URL || 'https://app.example.test');
  const btn = await orx(page, 'login_page.login_button');
  await btn.click();
  await expect(page).toHaveURL(/dashboard/);
});
"""


@pytest.fixture
def repo(tmp_path):
    import backend.repository.project_repository as repo_module

    previous = repo_module._active_project_repo
    repo_module._active_project_repo = JSONProjectRepository(tmp_path / "store.json")
    yield repo_module._active_project_repo
    repo_module._active_project_repo = previous


@pytest.fixture
def service(repo):
    return ObjectRepositoryService(project_repo=repo)


@pytest.fixture
def project(repo):
    return repo.create_project("Shop", "", framework="playwright", target_url="https://app.example.test")


def _test_case(repo, project, script=SCRIPT, **extra):
    requirement = repo.create_requirement(project.id, "Login", "Users log in", "high", "auth")
    scenario = Scenario(requirement_id=requirement.id, scenario_name="Login", description="Login", path_type="happy_path")
    repo.save_scenarios([scenario])
    case = TestCase(
        scenario_id=scenario.id,
        title="Login works",
        steps=["Open the login page", "Click Log in"],
        expected_result="The dashboard opens",
        playwright_script=script,
        **extra,
    )
    repo.save_test_cases([case])
    return case


def _login_button(service, project, locators=None):
    page = service.create_page(project.id, "login_page", "/login")
    _, element = service.create_element(
        project.id,
        page.id,
        "login_button",
        "button",
        locators or [
            {"strategy": "role", "value": "button", "options": {"name": "Log in"}},
            {"strategy": "css", "value": "#login"},
        ],
    )
    return page, element


def _element(service, project, element_id):
    return service.load(project.id).find_element(element_id)[1]


# ------------------------------------------------------------------ AC-4


@pytest.mark.parametrize(
    "name, pattern, locators, element_type",
    [
        ("LoginPage", "/login", [{"strategy": "css", "value": "#a"}], "button"),
        ("login", "login", [{"strategy": "css", "value": "#a"}], "button"),
        ("login", "/a/***", [{"strategy": "css", "value": "#a"}], "button"),
        ("login", "/a?b", [{"strategy": "css", "value": "#a"}], "button"),
        ("login", "/login", [], "button"),
        ("login", "/login", [{"strategy": "css", "value": f"#{i}"} for i in range(6)], "button"),
        ("login", "/login", [{"strategy": "magic", "value": "#a"}], "button"),
        ("login", "/login", [{"strategy": "css", "value": "  "}], "button"),
        ("login", "/login", [{"strategy": "css", "value": "#a"}], "widget"),
        ("login", "/login", [{"strategy": "css", "value": "#a", "options": {"name": "x"}}], "button"),
    ],
)
def test_invalid_input_is_rejected(service, project, name, pattern, locators, element_type):
    with pytest.raises(ObjectRepositoryInvalid):
        page = service.create_page(project.id, name, pattern)
        service.create_element(project.id, page.id, "submit", element_type, locators)


def test_duplicates_conflict(service, project):
    page, _ = _login_button(service, project)
    with pytest.raises(ObjectRepositoryConflict):
        service.create_page(project.id, "login_page", "/other")
    with pytest.raises(ObjectRepositoryConflict):
        service.create_page(project.id, "other", "/login")
    with pytest.raises(ObjectRepositoryConflict):
        service.create_element(project.id, page.id, "login_button", "button", [{"strategy": "css", "value": "#x"}])


def test_tester_update_resets_health_and_keeps_unchanged_counters(service, project):
    _, element = _login_button(service, project)
    service.apply_run_markers(project.id, uuid4(), ['[OBJREPO] {"key": "login_page.login_button", "index": 1, "tried": 2, "ok": true}'])
    element = _element(service, project, element.id)
    assert element.health == "needs_review"
    css = element.locators[1]
    assert css.success_count == 1

    _, updated, _ = service.update_element(project.id, element.id, {"locators": [
        {"strategy": "css", "value": "#login"},
        {"strategy": "text", "value": "Log in", "options": {"exact": True}},
    ]})
    assert updated.manually_edited is True
    assert updated.health == "healthy"
    assert updated.failure_count == 0
    assert updated.locators[0].id == css.id
    assert updated.locators[0].success_count == 1
    assert updated.locators[1].success_count == 0


# --------------------------------------------------------- page matching


def test_glob_matching_and_best_match(service, project):
    assert pattern_matches("/booking/*/details", "/booking/42/details/")
    assert not pattern_matches("/booking/*/details", "/booking/42/7/details")
    assert pattern_matches("/booking/**", "/booking/42/7/details")
    assert pattern_matches("/", "/")
    assert pattern_matches("/**", "/")
    assert not pattern_matches("/Login", "/login")
    wide = service.create_page(project.id, "booking_any", "/booking/**")
    exact = service.create_page(project.id, "booking_details", "/booking/*/details")
    pages = service.load(project.id).pages
    assert [p.id for p in matching_pages(pages, "/booking/9/details")] == [exact.id, wide.id]
    assert page_pattern_for_path("/booking/12345/details") == "/booking/*/details"
    assert page_pattern_for_path(f"/u/{uuid4()}") == "/u/*"


# ------------------------------------------------------------- AC-8, AC-9


def test_render_then_strip_returns_the_original_and_is_idempotent():
    locators = {"login_page.login_button": [{"strategy": "css", "value": "#login", "options": {}}]}
    rendered = scripts.render_script(SCRIPT, locators, 3000)
    assert scripts.JS_START in rendered
    assert rendered.index("import { test") < rendered.index(scripts.JS_START)
    assert scripts.strip_block(rendered) == SCRIPT
    assert scripts.render_script(rendered, locators, 3000) == rendered
    doubled = rendered.replace(scripts.JS_END, scripts.JS_END + "\n" + rendered.split("\n", 2)[2], 1)
    assert scripts.strip_block(doubled).count("orx(page, 'login_page.login_button')") >= 1
    assert scripts.JS_START not in scripts.strip_block(doubled)
    hand_edited = rendered.replace(scripts.JS_END + "\n", "")
    assert scripts.strip_block(hand_edited) == SCRIPT


def test_hostile_locator_text_cannot_trip_the_engine_danger_check():
    locators = {"login_page.login_button": [{"strategy": "text", "value": 'eval("x") Function(1) \'q\'', "options": {}}]}
    rendered = scripts.render_script(SCRIPT, locators, 3000)
    block = rendered[rendered.index(scripts.JS_START): rendered.index(scripts.JS_END)]
    assert "eval(" not in block
    assert "Function(" not in block
    assert "require(" not in block
    assert "\\u0028" in block


def test_selenium_render_translates_strategies():
    script = "from selenium import webdriver\nimport pytest\n\ndef test_login(driver):\n    orx(driver, 'login_page.login_button').click()\n"
    locators = {"login_page.login_button": [
        {"strategy": "role", "value": "button", "options": {"name": "Log in"}},
        {"strategy": "test_id", "value": "submit", "options": {}},
    ]}
    rendered = scripts.render_script(script, locators, 3000)
    assert scripts.PY_START in rendered and "def orx(driver, key):" in rendered
    assert scripts.strip_block(rendered) == script
    assert scripts.selenium_selector(locators["login_page.login_button"][0]) == (
        "xpath", '//*[(@role="button" or self::button) and (normalize-space()="Log in" or @aria-label="Log in")]'
    )
    compile(rendered, "<selenium>", "exec")


def test_api_renders_current_locators_and_save_strips_the_block(repo, service, project):
    _, element = _login_button(service, project)
    case = _test_case(repo, project)
    client = TestClient(app)
    url = f"/api/v1/projects/{project.id}/testcases/{case.id}"

    first = client.get(url).json()["playwright_script"]
    assert '"v": "#login"' in first

    # Locator fix: the next read shows the new locator with no regeneration.
    service.update_element(project.id, element.id, {"locators": [{"strategy": "css", "value": "#sign-in"}]})
    second = client.get(url).json()["playwright_script"]
    assert "#sign-in" in second and "#login" not in second

    saved = client.put(f"{url}/script", json={"script": second})
    assert saved.status_code == 200
    assert repo.get_test_case(case.id).playwright_script == SCRIPT

    workspace = client.get("/api/v1/playwright/workspace", params={"project_id": str(project.id), "test_case_id": str(case.id)})
    assert "#sign-in" in workspace.json()["playwright_script"]


def test_execution_agent_dispatches_rendered_scripts(repo, service, project, fake_playwright_runner):
    from backend.agents.execution_agent import ExecutionAgent
    from backend.models.state import WorkflowState

    _login_button(service, project)
    case = _test_case(repo, project, evaluation_status=EvaluationStatus.APPROVED)
    requirement = repo.get_requirements(project.id)[0]
    seen = []

    class Recorder:
        def run(self, script, run_id, **kwargs):
            seen.append(script)
            return fake_playwright_runner.run(script, run_id, **kwargs)

    ExecutionAgent(runner=Recorder(), project_id=project.id, repo=repo).run(
        WorkflowState(requirement=requirement, generated_test_cases=[case])
    )
    assert seen and scripts.JS_START in seen[0] and '"v": "#login"' in seen[0]


# ------------------------------------------------------------------ AC-10


def _marker(index, tried, ok, key="login_page.login_button"):
    return f'[OBJREPO] {{"key": "{key}", "index": {index}, "tried": {tried}, "ok": {"true" if ok else "false"}}}'


def test_run_markers_update_health_and_counters(service, project):
    _, element = _login_button(service, project)
    execution = uuid4()
    assert service.apply_run_markers(project.id, execution, [_marker(0, 1, True)]) == 1
    element = _element(service, project, element.id)
    assert element.health == "healthy"
    assert element.last_execution_id == execution
    assert element.last_locator_index == 0
    assert element.locators[0].success_count == 1

    # Duplicate delivery of the same execution changes nothing.
    assert service.apply_run_markers(project.id, execution, [_marker(0, 1, True)]) == 0
    assert _element(service, project, element.id).locators[0].success_count == 1

    service.apply_run_markers(project.id, uuid4(), [_marker(1, 2, True)])
    element = _element(service, project, element.id)
    assert element.health == "needs_review"
    assert element.locators[0].failure_count == 1
    assert element.locators[1].success_count == 1


def test_repeated_key_counts_once_with_the_worst_outcome_and_two_failed_runs_break(service, project):
    _, element = _login_button(service, project)
    lines = [_marker(0, 1, True)] * 19 + [_marker(2, 2, False)]
    service.apply_run_markers(project.id, uuid4(), lines)
    element = _element(service, project, element.id)
    assert element.health == "needs_review"
    assert element.failure_count == 1
    assert element.consecutive_failed_runs == 1
    assert [loc.failure_count for loc in element.locators] == [1, 1]
    assert element.locators[0].success_count == 0

    service.apply_run_markers(project.id, uuid4(), lines)
    element = _element(service, project, element.id)
    assert element.health == "broken"
    assert element.consecutive_failed_runs == 2

    service.apply_run_markers(project.id, uuid4(), [_marker(0, 1, True)])
    element = _element(service, project, element.id)
    assert element.health == "healthy" and element.consecutive_failed_runs == 0


def test_malformed_and_unknown_markers_are_skipped(service, project):
    _login_button(service, project)
    lines = ["[OBJREPO] {not json", '[OBJREPO] {"key": "Bad Key", "index": 0, "tried": 1, "ok": true}', _marker(0, 1, True, key="ghost.page")]
    assert service.apply_run_markers(project.id, uuid4(), lines) == 0


def test_completed_webhook_applies_markers_once(repo, service, project):
    _, element = _login_button(service, project)
    case = _test_case(repo, project)
    payload = {
        "event": "completed",
        "execution_id": str(uuid4()),
        "test_case_id": str(case.id),
        "status": "passed",
        "logs": [_marker(1, 2, True)],
    }
    client = TestClient(app)
    for _ in range(2):
        assert client.post(f"/api/v1/projects/{project.id}/executions/webhook", json=payload).status_code == 200
    element = _element(service, project, element.id)
    assert element.health == "needs_review"
    assert element.locators[1].success_count == 1


# --------------------------------------------------------------- AC-5


def test_rename_rewrites_every_script_and_delete_counts_references(repo, service, project):
    page, element = _login_button(service, project)
    frozen = _test_case(repo, project, is_frozen=True)
    other = _test_case(repo, project, script="test('x', async ({ page }) => { await orx(page, \"login_page.login_button\"); });")
    untouched = _test_case(repo, project, script="test('y', async () => {});")

    _, renamed, rewritten = service.update_element(project.id, element.id, {"name": "sign_in"})
    assert rewritten == 2
    assert "'login_page.sign_in'" in repo.get_test_case(frozen.id).playwright_script
    assert '"login_page.sign_in"' in repo.get_test_case(other.id).playwright_script
    assert repo.get_test_case(untouched.id).playwright_script == "test('y', async () => {});"

    _, rewritten = service.update_page(project.id, page.id, {"name": "auth"})
    assert rewritten == 2
    assert "'auth.sign_in'" in repo.get_test_case(frozen.id).playwright_script

    assert service.delete_element(project.id, renamed.id) == 2
    assert service.load(project.id).element_by_key("auth.sign_in") is None


# --------------------------------------------------------------- AC-6, AC-7


def test_prompt_elements_filter_order_and_cap(repo, service, project, monkeypatch):
    page = service.create_page(project.id, "home", "/")
    for name, health in [("a_broken", "broken"), ("b_missing", "missing"), ("c_review", "needs_review"), ("d_ok", "healthy")]:
        _, element = service.create_element(project.id, page.id, name, "button", [{"strategy": "css", "value": f"#{name}"}])

        def set_health(unit, element_id=element.id, health=health):
            unit.repository.find_element(element_id)[1].health = health

        service.store.mutate(project.id, set_health)
    case = _test_case(repo, project)
    keys = [item["key"] for item in service.prompt_elements(project, case)]
    assert keys == ["home.d_ok", "home.c_review", "home.b_missing"]

    from backend.config.settings import get_settings
    monkeypatch.setattr(get_settings().object_repository, "max_prompt_elements", 1)
    assert [item["key"] for item in service.prompt_elements(project, case)] == ["home.d_ok"]
    section = service.prompt_section(project, case)
    assert "orx(page, '<page>.<element>')" in section


def test_unmapped_project_gets_no_prompt_section(repo, service, project):
    case = _test_case(repo, project)
    assert service.prompt_section(project, case) is None


def test_unknown_keys_include_run_time_keys(service, project):
    _login_button(service, project)
    script = "await orx(page, 'login_page.login_button'); await orx(page, 'login_page.ghost'); await orx(page, `login_page.${name}`);"
    assert service.unknown_keys(project.id, script) == ["login_page.ghost", "`login_page.${name}`"]


# ------------------------------------------------------- AC-1, AC-2, AC-15


def _recon(label, testid="", locators=None, element_type="button", role="button"):
    return {
        "element_type": element_type,
        "role": role,
        "label": label,
        "placeholder": "",
        "attributes": {"data-testid": testid, "id": "", "name": "", "tag": "button", "type": ""},
        "locators": locators or [{"strategy": "role", "value": role, "options": {"name": label}, "match_count": 1}],
    }


def test_recon_ingest_creates_page_and_merges_on_rescan(service, project):
    url = "https://app.example.test/booking/12345/details"
    counts = service.ingest_recon(project.id, url, [
        _recon("Pay now"),
        _recon("Cancel"),
        _recon("Help"),
        _recon("", locators=[{"strategy": "css", "value": "div > button", "match_count": 2},
                             {"strategy": "xpath", "value": "//button[3]", "match_count": 2}]),
    ])
    assert counts == {"added": 4, "updated": 0, "marked_missing": 0}
    page = service.load(project.id).active_pages()[0]
    assert (page.name, page.url_pattern) == ("booking_details", "/booking/*/details")
    by_name = {e.name: e for e in page.elements}
    assert set(by_name) == {"pay_now", "cancel", "help", "button_1"}
    assert by_name["button_1"].fingerprint is None
    assert by_name["button_1"].health == "needs_review"

    # A tester owns "cancel"; "help" is deleted; "pay_now" changes locators.
    service.update_element(project.id, by_name["cancel"].id, {"locators": [{"strategy": "css", "value": "#cancel"}]})
    service.delete_element(project.id, by_name["help"].id)
    counts = service.ingest_recon(project.id, "https://app.example.test/booking/999/details", [
        _recon("Pay now", locators=[
            {"strategy": "css", "value": "#pay", "match_count": 1},
            {"strategy": "role", "value": "button", "options": {"name": "Pay now"}, "match_count": 1},
        ]),
        _recon("Cancel", locators=[{"strategy": "css", "value": "#recon-cancel", "match_count": 1}]),
        _recon("Help"),
    ])
    page = service.load(project.id).active_pages()[0]
    by_name = {e.name: e for e in page.elements if not e.is_deleted}
    assert counts["updated"] == 1
    assert by_name["pay_now"].locators[0].value == "#pay"
    assert by_name["cancel"].locators[0].value == "#cancel"
    assert by_name["button_1"].health == "missing"
    assert "help" not in by_name and len([e for e in page.elements if e.name == "help"]) == 1

    counts = service.ingest_recon(project.id, url, [_recon("Pay now", locators=[
        {"strategy": "css", "value": "#pay", "match_count": 1},
        {"strategy": "role", "value": "button", "options": {"name": "Pay now"}, "match_count": 1},
    ])])
    assert counts["updated"] == 0


def test_recon_puts_a_unique_locator_first_and_drops_unmatched():
    ordered, ambiguous = ObjectRepositoryService.order_recon_locators([
        {"strategy": "test_id", "value": "pay", "match_count": 0},
        {"strategy": "role", "value": "button", "options": {"name": "Pay"}, "match_count": 3},
        {"strategy": "css", "value": "#pay", "match_count": 1},
    ])
    assert [loc["strategy"] for loc in ordered] == ["css", "role"]
    assert ambiguous is False


def test_tester_edit_wins_over_recon(service, project):
    url = "https://app.example.test/checkout"
    service.ingest_recon(project.id, url, [_recon("Pay")])
    element = service.load(project.id).active_pages()[0].elements[0]
    original = service.store.mutate

    def tester_first(project_id, change):
        # The tester saves just before recon takes the lock.
        if change.__qualname__.endswith("ingest_recon.<locals>.change"):
            service.store.mutate = original
            service.update_element(project_id, element.id, {"locators": [{"strategy": "css", "value": "#tester"}]})
        return original(project_id, change)

    service._store = service.store
    service._store.mutate = tester_first
    service.ingest_recon(project.id, url, [_recon("Pay", locators=[{"strategy": "css", "value": "#recon", "match_count": 1}])])
    element = _element(service, project, element.id)
    assert element.manually_edited is True
    assert element.locators[0].value == "#tester"


def test_deleted_page_pattern_is_not_recreated(service, project):
    page = service.create_page(project.id, "checkout", "/checkout")
    service.delete_page(project.id, page.id)
    counts = service.ingest_recon(project.id, "https://app.example.test/checkout", [_recon("Pay")])
    assert counts == {"added": 0, "updated": 0, "marked_missing": 0}
    assert service.load(project.id).active_pages() == []


# ----------------------------------------------------------------- AC-3


def test_scan_guards_and_status(repo, service, project):
    from backend.services.object_repository_scan import ObjectRepositoryScanService
    from backend.services.object_repository_service import ObjectRepositoryNotFound

    ObjectRepositoryScanService._scans.clear()
    audits = []

    class Inspector:
        def inspect_urls(self, project_id, urls):
            return {"pages": [{"url": urls[0]}], "_object_repository": [
                {"url": "https://app.example.test/login", "elements": [_recon("Log in")]},
            ]}

    scanner = ObjectRepositoryScanService(service, inspector=Inspector(), audit=lambda *args: audits.append(args))
    with pytest.raises(ObjectRepositoryConflict):
        scanner.start(project.id)

    project.is_safe_test_environment = True
    repo.update_project(project)
    scan_id = scanner.start(project.id)
    assert scanner.status(project.id, scan_id)["status"] == "queued"
    with pytest.raises(ObjectRepositoryConflict):
        scanner.start(project.id)
    with pytest.raises(ObjectRepositoryNotFound):
        scanner.status(uuid4(), scan_id)

    scanner.run(scan_id)
    status = scanner.status(project.id, scan_id)
    assert status["status"] == "succeeded"
    assert (status["pages_scanned"], status["added"]) == (1, 1)
    assert audits and audits[0][2]["added"] == 1
    assert service.load(project.id).element_by_key("login.log_in") is not None


# ------------------------------------------------------------- API, AC-11


def test_api_create_and_list(repo, project):
    client = TestClient(app)
    base = f"/api/v1/projects/{project.id}/object-repository"
    page = client.post(f"{base}/pages", json={"name": "login_page", "url_pattern": "/login"})
    assert page.status_code == 201
    page_id = page.json()["id"]
    assert client.post(f"{base}/pages", json={"name": "login_page", "url_pattern": "/x"}).status_code == 409
    assert client.post(f"{base}/pages", json={"name": "Bad", "url_pattern": "/x"}).status_code == 422

    element = client.post(f"{base}/pages/{page_id}/elements", json={
        "name": "login_button",
        "element_type": "button",
        "locators": [{"strategy": "role", "value": "button", "options": {"name": "Log in"}}],
    })
    assert element.status_code == 201
    assert element.json()["key"] == "login_page.login_button"
    assert element.json()["manually_edited"] is True

    pages = client.get(f"{base}/pages").json()
    assert pages["total"] == 1 and pages["pages"][0]["counts"]["healthy"] == 1
    listed = client.get(f"{base}/pages/{page_id}/elements", params={"health": "healthy"}).json()
    assert listed["total"] == 1

    patched = client.patch(f"{base}/elements/{element.json()['id']}", json={"description": "Main login"})
    assert patched.status_code == 200 and patched.json()["scripts_rewritten"] == 0
    assert client.delete(f"{base}/elements/{element.json()['id']}").json() == {"referencing_test_cases": 0}
    assert client.get(f"{base}/pages/{uuid4()}/elements").status_code == 404
    assert client.post(f"{base}/scan").status_code == 409


@pytest.fixture
def seeded():
    from backend.database.db import SessionLocal
    from backend.database.db_seeder import seed_database

    db = SessionLocal()
    seed_database(db)
    db.close()


def _token(client, email, password):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password}).json()["access_token"]


def test_permissions(seeded, repo, service, project):
    _, element = _login_button(service, project)
    client = TestClient(app)
    base = f"/api/v1/projects/{project.id}/object-repository"
    stakeholder = {"Authorization": f"Bearer {_token(client, 'stakeholder@agenticai.com', 'stakeholderpassword')}"}
    tester = {"Authorization": f"Bearer {_token(client, 'tester@agenticai.com', 'testerpassword')}"}

    assert client.get(f"{base}/pages", headers=stakeholder).status_code == 200
    assert client.get(f"{base}/permissions", headers=stakeholder).json() == {"actions": ["view"]}
    assert client.get(f"{base}/permissions", headers=tester).json() == {"actions": ["view", "create", "edit"]}
    assert client.patch(f"{base}/elements/{element.id}", json={"description": "x"}, headers=stakeholder).status_code == 403
    assert client.patch(f"{base}/elements/{element.id}", json={"description": "x"}, headers=tester).status_code == 200
    assert client.delete(f"{base}/elements/{element.id}", headers=tester).status_code == 403


# ----------------------------------------------------------------- AC-14


@pytest.fixture
def sql_store():
    """The PostgreSQL store's code run against the SQLAlchemy engine (SQLite under the JSON provider)."""
    from backend.database.db import Base, SessionLocal, engine
    from backend.database.db_models import ProjectDB
    from backend.repository.postgres_object_repository_store import PostgresObjectRepositoryStore

    Base.metadata.create_all(engine)
    db = SessionLocal()
    project = ProjectDB(name=f"objrepo-{uuid4()}", description="", target_url=f"https://{uuid4().hex}.example.test")
    db.add(project)
    db.commit()
    project_id = project.id
    db.close()
    yield PostgresObjectRepositoryStore(), project_id
    db = SessionLocal()
    db.query(ProjectDB).filter(ProjectDB.id == project_id).delete()
    db.commit()
    db.close()


class _Projects:
    def __init__(self, project_id):
        from backend.models.project import Project

        self.project = Project(id=project_id, name="sql", target_url="https://app.example.test")

    def get_project(self, project_id):
        return self.project if project_id == self.project.id else None


def test_sql_store_matches_the_json_rules(sql_store):
    store, project_id = sql_store
    service = ObjectRepositoryService(project_repo=_Projects(project_id), store=store)
    page = service.create_page(project_id, "login_page", "/login")
    _, element = service.create_element(project_id, page.id, "login_button", "button", [
        {"strategy": "role", "value": "button", "options": {"name": "Log in"}},
        {"strategy": "css", "value": "#login"},
    ])
    with pytest.raises(ObjectRepositoryConflict):
        service.create_element(project_id, page.id, "login_button", "button", [{"strategy": "css", "value": "#x"}])

    service.apply_run_markers(project_id, uuid4(), [_marker(1, 2, True)])
    # Reorder: the css locator moves to position 0 and keeps its counters.
    service.update_element(project_id, element.id, {"locators": [
        {"strategy": "css", "value": "#login"},
        {"strategy": "role", "value": "button", "options": {"name": "Log in"}},
    ]})
    loaded = store.load(project_id).find_element(element.id)[1]
    assert [(loc.position, loc.strategy, loc.success_count) for loc in loaded.locators] == [(0, "css", 1), (1, "role", 0)]
    assert loaded.health == "healthy" and loaded.manually_edited

    service.ingest_recon(project_id, "https://app.example.test/login", [_recon("Help")])
    assert store.load(project_id).element_by_key("login_page.help") is not None


def test_sql_store_rolls_back_after_an_error(sql_store):
    from sqlalchemy.exc import SQLAlchemyError

    store, project_id = sql_store
    service = ObjectRepositoryService(project_repo=_Projects(project_id), store=store)
    service.create_page(project_id, "home", "/")

    def broken(unit):
        unit.repository.pages[0].name = "renamed"
        raise SQLAlchemyError("injected")

    with pytest.raises(SQLAlchemyError):
        store.mutate(project_id, broken)
    assert store.load(project_id).pages[0].name == "home"
    service.create_page(project_id, "next", "/next")
    assert {p.name for p in store.load(project_id).pages} == {"home", "next"}


# ------------------------------------------- harvest during execution, AC-16 to AC-21

import json  # noqa: E402

from backend.services import object_repository_harvest as harvest  # noqa: E402
from backend.services.execution_service import ExecutionService  # noqa: E402

TOKEN_ID = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def vault(monkeypatch):
    from backend.services.vault_service import VaultService

    monkeypatch.setattr(VaultService, "get_credentials", lambda self, project_id: ("standard_user", "secret_sauce"))


def _snapshot(url, *elements):
    return {"url": url, "elements": list(elements)}


def test_harvest_ingest_adds_and_updates_but_never_marks_missing(service, project):
    url = "https://app.example.test/inventory.html"
    service.ingest_recon(project.id, url, [_recon("Add"), _recon("Remove")])
    page = service.load(project.id).active_pages()[0]
    scanned_at = page.last_scanned_at
    remove = next(e for e in page.elements if e.name == "remove")

    counts = service.ingest_recon(project.id, "/inventory.html", [_recon("Add"), _recon("Checkout")], mark_missing=False)
    assert counts == {"added": 1, "updated": 0, "marked_missing": 0}
    page = service.load(project.id).active_pages()[0]
    assert page.last_scanned_at == scanned_at
    assert _element(service, project, remove.id).health == "healthy"

    # A scan marks it missing; a run that sees it again brings it back.
    service.ingest_recon(project.id, url, [_recon("Add"), _recon("Checkout")])
    assert _element(service, project, remove.id).health == "missing"
    service.ingest_recon(project.id, "/inventory.html", [_recon("Remove")], mark_missing=False)
    assert _element(service, project, remove.id).health == "healthy"

    # The same snapshot again changes nothing.
    again = service.ingest_recon(project.id, "/inventory.html", [_recon("Add"), _recon("Checkout")], mark_missing=False)
    assert again == {"added": 0, "updated": 0, "marked_missing": 0}


def test_clean_elements_redacts_drops_redacted_locators_and_tokens():
    elements = [
        {
            **_recon("Welcome STANDARD_USER"),
            "attributes": {"data-testid": "", "id": TOKEN_ID, "name": "", "tag": "h2", "type": ""},
            "locators": [
                {"strategy": "role", "value": "button", "options": {"name": "Welcome STANDARD_USER"}, "match_count": 1},
                {"strategy": "css", "value": f"#{TOKEN_ID}", "options": {}, "match_count": 1},
                {"strategy": "css", "value": "h2.greeting", "options": {}, "match_count": 1},
            ],
        },
        _recon("Only standard_user"),
        _recon("Go", locators=[{"strategy": "text", "value": "Go", "options": {}, "match_count": 1}]),
    ]
    cleaned = harvest.clean_elements(elements, ["standard_user", "Go", None, 7])
    assert [e["label"] for e in cleaned] == ["Welcome [redacted]", "Go"]
    assert cleaned[0]["attributes"]["id"] == ""
    assert [loc["value"] for loc in cleaned[0]["locators"]] == ["h2.greeting"]
    # Values shorter than 3 characters redact nothing.
    assert cleaned[1]["locators"][0]["value"] == "Go"


def test_parse_snapshots_keeps_the_first_per_path_caps_pages_and_skips_malformed():
    snapshots = [
        _snapshot("/cart/", _recon("First")),
        _snapshot("https://app.example.test/cart#/x", _recon("Second")),
        "not a snapshot",
        {"url": "/broken"},
        _snapshot("/Cart", _recon("Case kept"), {"element_type": "spaceship", "locators": []}),
        _snapshot("/a", _recon("A")),
        _snapshot("/b", _recon("B")),
    ]
    pages = harvest.parse_snapshots(snapshots, max_pages=3)
    assert [path for path, _ in pages] == ["/cart", "/Cart", "/a"]
    assert [e["label"] for e in pages[0][1]] == ["First"]
    assert [e["label"] for e in pages[1][1]] == ["Case kept"]
    assert harvest.parse_snapshots({"url": "/x"}, 5) == []


def test_apply_harvest_redacts_vault_credentials_and_test_data(repo, service, project, vault):
    case = _test_case(repo, project, test_data={"promo": "Promo42"}, test_data_sets=[{"q": "x"}, {"city": "Lisbon"}])
    snapshots = [
        _snapshot(
            "https://app.example.test/inventory.html?user=standard_user",
            _recon("Welcome standard_user"),
            _recon("Apply PROMO42 now"),
            _recon("Flights to lisbon", testid="lisbon-flights"),
            _recon("Search x"),
            {**_recon("Checkout"), "attributes": {"data-testid": "", "id": TOKEN_ID, "name": "", "tag": "button", "type": ""}},
        )
    ]
    totals = ExecutionService().apply_object_repository_harvest(project.id, case.id, uuid4(), snapshots)
    assert totals["pages"] == 1
    stored = service.load(project.id).model_dump_json().lower()
    for secret in ("standard_user", "secret_sauce", "promo42", "lisbon", TOKEN_ID):
        assert secret not in stored
    page = service.load(project.id).active_pages()[0]
    assert page.url_pattern == "/inventory.html"
    assert {e.name for e in page.elements} == {"search_x", "checkout"}


def test_completed_webhook_ingests_harvest_from_passed_and_failed_runs(repo, service, project, vault):
    case = _test_case(repo, project)
    client = TestClient(app)
    for status, path in (("passed", "/inventory.html"), ("failed", "/cart.html")):
        payload = {
            "event": "completed",
            "execution_id": str(uuid4()),
            "test_case_id": str(case.id),
            "status": status,
            "logs": [],
            "harvest": [_snapshot(path, _recon(f"Open {path}"))],
        }
        assert client.post(f"/api/v1/projects/{project.id}/executions/webhook", json=payload).status_code == 200
    assert {p.url_pattern for p in service.load(project.id).active_pages()} == {"/inventory.html", "/cart.html"}

    garbage = {"event": "completed", "execution_id": str(uuid4()), "test_case_id": str(case.id), "status": "passed", "harvest": "nope"}
    assert client.post(f"/api/v1/projects/{project.id}/executions/webhook", json=garbage).status_code == 200


def test_harvest_kill_switch_turns_off_both_runners_and_ingest(repo, service, project, vault, monkeypatch):
    from backend.config.settings import get_settings
    from backend.services.playwright_runner import PlaywrightRunner

    monkeypatch.setattr(get_settings().object_repository, "harvest_enabled", False)
    assert PlaywrightRunner._harvest_env()["OBJREPO_HARVEST"] == "0"
    assert TestClient(app).get("/api/v1/object-repository/harvest-settings").json()["enabled"] is False
    totals = ExecutionService().apply_object_repository_harvest(project.id, None, uuid4(), [_snapshot("/a", _recon("A"))])
    assert totals["pages"] == 0 and service.load(project.id).active_pages() == []


def test_harvest_settings_route_and_runner_environment():
    from backend.services.playwright_runner import PlaywrightRunner

    body = TestClient(app).get("/api/v1/object-repository/harvest-settings").json()
    assert body == {"enabled": True, "max_pages": 20, "budget_ms": 5000}
    assert PlaywrightRunner._harvest_env() == {
        "OBJREPO_HARVEST": "1",
        "OBJREPO_HARVEST_MAX_PAGES": "20",
        "OBJREPO_HARVEST_BUDGET_MS": "5000",
    }
    assert json.loads(PlaywrightRunner._harvest_source_literal()).startswith("(options) =>")


def test_read_harvest_file_skips_missing_and_malformed_files(tmp_path):
    assert harvest.read_harvest_file(tmp_path) == []
    (tmp_path / "harvest.json").write_text("{not json", encoding="utf-8")
    assert harvest.read_harvest_file(tmp_path) == []
    (tmp_path / "harvest.json").write_text('{"url": "/a"}', encoding="utf-8")
    assert harvest.read_harvest_file(tmp_path) == []
    (tmp_path / "harvest.json").write_text(json.dumps([_snapshot("/a", _recon("A"))]), encoding="utf-8")
    assert harvest.read_harvest_file(tmp_path)[0]["url"] == "/a"


def test_batch_route_attributes_each_harvest_file_to_its_own_test_case(repo, service, project, vault, tmp_path, monkeypatch):
    import backend.services.playwright_runner as runner_module
    from backend.services.playwright_runner import PlaywrightRunResult

    first = _test_case(repo, project, test_data={"user": "alice_first"})
    second = _test_case(repo, project, test_data={"user": "bob_second"})
    pages = {str(first.id): "/first", str(second.id): "/second"}

    class HarvestingRunner:
        def run(self, script, run_id, **kwargs):
            run_dir = tmp_path / "artifacts" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            elements = [_recon("Hello alice_first"), _recon("Hello bob_second"), _recon(f"Open {pages[run_id]}")]
            (run_dir / "harvest.json").write_text(json.dumps([_snapshot(pages[run_id], *elements)]), encoding="utf-8")
            return PlaywrightRunResult(status="passed", duration_seconds=1.0)

    monkeypatch.setattr(runner_module, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(runner_module, "PlaywrightRunner", HarvestingRunner)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "backend.services.report_service.ReportService.compile_reports_with_engine_selection", lambda *a, **k: None
    )
    response = TestClient(app).post(
        f"/api/v1/projects/{project.id}/batches/execute", json=[str(first.id), str(second.id)]
    )
    assert response.status_code == 200
    by_pattern = {p.url_pattern: {e.name for e in p.elements} for p in service.load(project.id).active_pages()}
    # Each file is redacted with its own test case's data (the redacted
    # element had only that locator, so it is dropped).
    assert by_pattern["/first"] == {"hello_bob_second", "open_first"}
    assert by_pattern["/second"] == {"hello_alice_first", "open_second"}
