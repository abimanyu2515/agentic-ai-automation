"""Object repository harvest against a real browser (spec 0004, AC-16, AC-17, AC-21).

These launch Chromium, so they skip when no browser is installed.
"""

import pytest

from backend.services.object_repository_harvest import extractor_source

PARITY_PAGE = """
<html><body>
  <nav><a href="/home">Home</a><a href="/cart" aria-label="Open cart">Cart</a></nav>
  <form>
    <label for="user">Username</label><input id="user" name="user-name" placeholder="Your username">
    <label>Password <input type="password" name="password" placeholder="Password"></label>
    <input type="search" placeholder="Search products" aria-label="Search">
    <input type="number" name="qty" aria-label="Quantity">
    <select name="sort" aria-label="Sort by"><option>Name</option></select>
    <textarea data-testid="notes" placeholder="Notes"></textarea>
    <input type="checkbox" id="remember"><label for="remember">Remember me</label>
    <input type="submit" value="Log in">
    <input type="submit">
  </form>
  <button data-testid="add-to-cart">Add to cart</button>
  <button>Add to cart</button>
  <button aria-label="Close dialog">x</button>
  <div role="button" tabindex="0">Custom   action</div>
  <button style="display:none">Hidden button</button>
  <div aria-hidden="true"><button>Ghost</button></div>
  <a href="/terms">Terms <span>and conditions</span></a>
  <a href="/very-long" id="0123456789abcdef0123456789abcdef01234567">Token link</a>
  <p>Plain text paragraph with Add to cart words</p>
</body></html>
"""


@pytest.fixture(scope="module")
def page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # pragma: no cover - depends on the machine
            pytest.skip(f"Chromium is not available: {exc}")
        page = browser.new_page()
        yield page
        browser.close()


def _extract(page, html):
    page.set_content(html)
    return page.evaluate(extractor_source(), {})


def _playwright_count(page, locator: dict) -> int:
    """How orx resolves each strategy (the injected block in spec 0004)."""
    strategy, value, options = locator["strategy"], locator["value"], locator.get("options") or {}
    if strategy == "role":
        kwargs = {key: options[key] for key in ("name", "exact") if key in options}
        return page.get_by_role(value, **kwargs).count()
    if strategy == "label":
        return page.get_by_label(value, exact=options.get("exact", False)).count()
    if strategy == "placeholder":
        return page.get_by_placeholder(value).count()
    if strategy == "text":
        return page.get_by_text(value, exact=options.get("exact", False)).count()
    if strategy == "test_id":
        return page.get_by_test_id(value).count()
    if strategy == "xpath":
        return page.locator(f"xpath={value}").count()
    return page.locator(value).count()


def test_extractor_counts_match_playwright_for_every_locator(page):
    result = _extract(page, PARITY_PAGE)
    checked = 0
    for element in result["elements"]:
        for locator in element["locators"]:
            assert locator["match_count"] == _playwright_count(page, locator), (element["label"], locator)
            checked += 1
    strategies = {loc["strategy"] for element in result["elements"] for loc in element["locators"]}
    assert {"role", "label", "placeholder", "test_id", "css"} <= strategies
    assert checked > 20


@pytest.mark.parametrize(
    ("strategy", "value", "options"),
    [
        ("text", "Add to cart", {}),
        ("text", "Terms and conditions", {}),
        ("text", "Custom action", {"exact": True}),
        ("role", "button", {"name": "add to cart"}),
        ("role", "button", {"name": "Add to cart", "exact": True}),
        ("role", "textbox", {}),
        ("role", "link", {"name": "Open cart"}),
        ("role", "searchbox", {"name": "Search"}),
        ("role", "spinbutton", {"name": "Quantity"}),
        ("role", "combobox", {"name": "Sort by"}),
        ("role", "checkbox", {"name": "Remember me"}),
        ("role", "button", {"name": "Submit"}),
        ("label", "Password", {}),
        ("label", "Close dialog", {}),
        ("label", "remember", {}),
        ("placeholder", "username", {}),
        ("xpath", "//button", {}),
    ],
)
def test_strategy_table_matches_playwright_for_strategies_recon_does_not_build(page, strategy, value, options):
    page.set_content(PARITY_PAGE)
    locator = {"strategy": strategy, "value": value, "options": options}
    counted = page.evaluate(extractor_source(), {"count": locator})
    assert counted == _playwright_count(page, locator)


def test_extractor_skips_hidden_elements_values_and_tokens(page):
    result = _extract(page, PARITY_PAGE)
    labels = [element["label"] for element in result["elements"]]
    assert "Hidden button" not in labels
    # aria-hidden still renders, so it is collected, but getByRole can't reach it.
    ghost = next(element for element in result["elements"] if element["label"] == "Ghost")
    assert all(loc["match_count"] == 0 for loc in ghost["locators"] if loc["strategy"] == "role")
    token_link = next(element for element in result["elements"] if element["label"] == "Token link")
    assert token_link["attributes"]["id"] == ""
    assert all("0123456789abcdef" not in loc["value"] for loc in token_link["locators"])
    # Nav links are saved as nav, not also as link.
    assert [e["element_type"] for e in result["elements"] if e["label"] == "Home"] == ["nav"]


def test_extractor_never_reads_typed_values(page):
    page.set_content(PARITY_PAGE)
    page.fill("#user", "typed_secret_value")
    page.fill("textarea", "another typed secret")
    result = page.evaluate(extractor_source(), {})
    assert "typed_secret_value" not in str(result) and "another typed secret" not in str(result)


def test_extractor_caps_elements_per_snapshot(page):
    html = "<html><body>" + "".join(f"<button>Item {i}</button>" for i in range(400)) + "</body></html>"
    assert len(_extract(page, html)["elements"]) == 300


def test_python_runner_harvests_each_page_once_across_datasets_and_worker_restarts(tmp_path, monkeypatch):
    """A real PlaywrightRunner run (slowMo 1000): three datasets over three pages,
    the first failing so Playwright restarts the worker (AC-16, AC-17, AC-18, AC-20)."""
    import shutil

    from backend.services.object_repository_harvest import parse_snapshots, read_harvest_file
    from backend.services.playwright_runner import ARTIFACTS_ROOT, PlaywrightRunner
    from backend.tests.harvest_site import serve_site

    if shutil.which("npx") is None:
        pytest.skip("npx is not available")
    monkeypatch.setenv("TARGET_USERNAME", "standard_user")
    monkeypatch.setenv("TARGET_PASSWORD", "secret_sauce")
    run_id = f"harvest-test-{tmp_path.name}"
    with serve_site(tmp_path) as base:
        script = f"""import {{ test }} from '@playwright/test';
for (const row of ['a', 'b', 'c']) {{
  test(`walk ${{row}}`, async ({{ page }}) => {{
    await page.goto('{base}/login.html');
    await page.fill('#user', process.env.TARGET_USERNAME || '');
    await page.click('#login');
    await page.waitForTimeout(1200);
    await page.click('#cart');
    await page.waitForTimeout(1200);
    if (row === 'a') throw new Error('dataset a fails');
  }});
}}
"""
        try:
            result = PlaywrightRunner(timeout_seconds=120).run(script, run_id)
            snapshots = read_harvest_file(ARTIFACTS_ROOT / run_id)
        finally:
            shutil.rmtree(ARTIFACTS_ROOT / run_id, ignore_errors=True)

    # Harvesting never changes the outcome: one dataset failed, so the run failed.
    assert result.status == "failed"
    assert sorted(s["url"] for s in snapshots) == ["/cart.html", "/inventory.html", "/login.html"]
    assert len(parse_snapshots(snapshots, 20)) == 3
    text = str(snapshots)
    assert "standard_user" not in text and "secret_sauce" not in text
    assert "Profile [redacted]" in text


# ------------------------------------------- live-verified scripts: key matching

LOGIN_PAGE = """
<html><body>
  <form>
    <input data-test="username" placeholder="Username">
    <input data-test="password" type="password" placeholder="Password">
    <input data-test="other" placeholder="Other">
    <button data-test="login-button" style="display:none">Log in</button>
    <input data-test="login-visible" type="submit" value="Login">
  </form>
</body></html>
"""


def _repository(url_pattern, elements):
    """One page holding elements given as (name, [(strategy, value, options), ...])."""
    from uuid import uuid4

    from backend.models.object_repository import (
        ObjectElement,
        ObjectLocator,
        ObjectPage,
        ProjectObjectRepository,
    )

    project_id = uuid4()
    page = ObjectPage(project_id=project_id, name="home", url_pattern=url_pattern)
    for name, locators in elements:
        page.elements.append(ObjectElement(
            page_id=page.id, name=name, element_type="input",
            locators=[
                ObjectLocator(position=index, strategy=strategy, value=value, options=options)
                for index, (strategy, value, options) in enumerate(locators)
            ],
        ))
    return ProjectObjectRepository(project_id=project_id, pages=[page])


def _resolve(repository, selectors, url="https://app.example.test/", **resolver_kwargs):
    """Each selector's key from ObjectKeyResolver on LOGIN_PAGE served at url."""
    import asyncio

    async_api = pytest.importorskip("playwright.async_api")
    from backend.services.object_repository_matcher import ObjectKeyResolver

    async def run():
        async with async_api.async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=True)
            except Exception as exc:  # pragma: no cover - depends on the machine
                pytest.skip(f"Chromium is not available: {exc}")
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.fulfill(content_type="text/html", body=LOGIN_PAGE))
            await page.goto(url)
            resolver = ObjectKeyResolver(repository, **resolver_kwargs)
            keys = [await resolver(page, selector) for selector in selectors]
            await browser.close()
            return keys

    # The module's sync `page` fixture keeps an event loop running on this
    # thread, so the async browser runs on its own thread.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, run()).result()


def test_resolver_matches_a_recorded_selector_to_the_element_on_the_same_node():
    repository = _repository("/", [
        ("username", [("role", "textbox", {"name": "Username"})]),
        ("password", [("placeholder", "Password", {})]),
    ])

    assert _resolve(repository, ['[data-test="username"]', '[data-test="password"]', '[data-test="other"]']) == [
        "home.username", "home.password", None,
    ]


def test_resolver_follows_orx_order_hidden_main_falls_back_and_wrong_node_stops():
    repository = _repository("/", [
        # The hidden main locator is skipped, as orx skips it; the backup finds the node.
        ("login", [("css", '[data-test="login-button"]', {}), ("role", "button", {"name": "Login"})]),
        # orx would act on the username field first, so this is not the "other" field.
        ("loose", [("css", "input", {}), ("css", '[data-test="other"]', {})]),
    ])

    assert _resolve(repository, ['[data-test="login-visible"]', '[data-test="other"]']) == ["home.login", None]


def test_resolver_ignores_pages_whose_pattern_does_not_match_the_url():
    repository = _repository("/inventory.html", [("username", [("role", "textbox", {"name": "Username"})])])

    assert _resolve(repository, ['[data-test="username"]']) == [None]
    assert _resolve(repository, ['[data-test="username"]'], url="https://app.example.test/inventory.html") == [
        "home.username"
    ]


def _empty_repository():
    from uuid import uuid4

    from backend.models.object_repository import ProjectObjectRepository

    return ProjectObjectRepository(project_id=uuid4(), pages=[])


def test_resolver_harvests_a_page_it_misses_once_and_then_writes_its_key(tmp_path):
    import backend.repository.project_repository as repo_module
    from backend.repository.project_repository import JSONProjectRepository
    from backend.services.object_repository_harvest import clean_elements
    from backend.services.object_repository_service import ObjectRepositoryService

    previous = repo_module._active_project_repo
    repo_module._active_project_repo = JSONProjectRepository(tmp_path / "store.json")
    try:
        repo = repo_module._active_project_repo
        project = repo.create_project("Shop", "", framework="playwright", target_url="https://app.example.test")
        service = ObjectRepositoryService(project_repo=repo)
        harvested = []

        def harvest(path, elements):
            harvested.append(path)
            service.ingest_recon(project.id, path, clean_elements(elements, []), mark_missing=False)
            return service.load(project.id)

        keys = _resolve(
            _empty_repository(),
            ['[data-test="username"]', '[data-test="nothing-here"]', '[data-test="password"]'],
            url="https://app.example.test/login",
            harvest=harvest,
        )
        stored = service.load(project.id).active_pages()
    finally:
        repo_module._active_project_repo = previous

    # One snapshot of /login, even though a later selector also missed.
    assert harvested == ["/login"]
    assert [page.url_pattern for page in stored] == ["/login"]
    assert keys[0] and keys[0].startswith("login.")
    assert keys[1] is None
    assert keys[2] and keys[2].startswith("login.") and keys[2] != keys[0]


def test_resolver_harvest_failure_and_disabled_harvest_keep_raw_selectors():
    def failing(path, elements):
        raise RuntimeError("store down")

    assert _resolve(_empty_repository(), ['[data-test="username"]'], harvest=failing) == [None]
    assert _resolve(_empty_repository(), ['[data-test="username"]'], harvest=failing, max_pages=0) == [None]
    assert _resolve(_empty_repository(), ['[data-test="username"]']) == [None]
