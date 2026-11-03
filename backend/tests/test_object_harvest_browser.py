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
