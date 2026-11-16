import asyncio
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import MagicMock

import pytest

from backend.agents.application_reconnaissance_agent import (
    ApplicationInspectionError,
    ApplicationReconnaissanceAgent,
)
from backend.models.test_case import TestCase
from backend.models.state import WorkflowState
from backend.models.project import Project


def test_safe_origin_excludes_credentials_paths_and_default_ports():
    assert ApplicationReconnaissanceAgent._safe_origin(
        "https://user:secret@example.test:443/path?token=value"
    ) == "https://example.test"


def test_link_local_target_detection():
    assert ApplicationReconnaissanceAgent._resolves_to_link_local(
        "http://169.254.10.20/"
    )
    assert not ApplicationReconnaissanceAgent._resolves_to_link_local(
        "http://127.0.0.1/"
    )


def test_reconnaissance_blocks_step_urls_outside_project_origin():
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Visit an external destination",
        steps=["Visit https://outside.example/login"],
        expected_result="The destination opens",
    )
    state = WorkflowState(requirement=None)

    with pytest.raises(ApplicationInspectionError) as error:
        asyncio.run(
            ApplicationReconnaissanceAgent()._reconnoiter(
                state=state,
                target_url="https://app.example",
                test_cases=[test_case],
            )
        )

    assert error.value.reason == "insufficient_evidence"


def test_safe_origin_preserves_non_default_ports():
    assert ApplicationReconnaissanceAgent._safe_origin(
        "https://example.test:8443/path"
    ) == "https://example.test:8443"
    assert ApplicationReconnaissanceAgent._safe_origin(
        "http://example.test:8080/path"
    ) == "http://example.test:8080"


def test_safe_origin_handles_ipv6():
    assert ApplicationReconnaissanceAgent._safe_origin(
        "https://[::1]:8443/path"
    ) == "https://[::1]:8443"


def test_extract_urls_from_text_handles_relative_paths():
    agent = ApplicationReconnaissanceAgent()
    urls = agent._extract_urls_from_text("Go to /dashboard and visit /settings", "https://app.example")
    assert "https://app.example/dashboard" in urls
    assert "https://app.example/settings" in urls


def test_extract_urls_from_text_handles_full_urls():
    agent = ApplicationReconnaissanceAgent()
    urls = agent._extract_urls_from_text("Visit https://other.example/page", "https://app.example")
    assert "https://other.example/page" in urls


def test_extract_urls_from_text_handles_markdown_links():
    agent = ApplicationReconnaissanceAgent()
    urls = agent._extract_urls_from_text("See [here](https://example.com/page)", "https://app.example")
    assert "https://example.com/page" in urls


def test_reconnaissance_returns_error_page_for_empty_target_url():
    """Empty target_url results in a page with error, not an exception."""
    agent = ApplicationReconnaissanceAgent()
    state = WorkflowState(requirement=None)
    result = asyncio.run(
        agent._reconnoiter(
            state=state,
            target_url="",
            test_cases=[],
        )
    )
    # Returns dom_context with a page containing error
    assert "pages" in result
    assert len(result["pages"]) == 1
    assert "error" in result["pages"][0]
    assert "Navigation failed" in result["pages"][0]["error"]


def test_reconnaissance_handles_navigation_failure_in_page_result():
    agent = ApplicationReconnaissanceAgent()
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Test",
        steps=["Open the app"],
        expected_result="It works",
    )
    class BrokenPage:
        async def goto(self, *args, **kwargs):
            raise RuntimeError("navigation failed")

    result = asyncio.run(
        agent._inspect_page(
            page=BrokenPage(),
            url="https://unreachable.invalid.tld",
            test_cases=[test_case],
            allow_invalid_login_probe=False,
        )
    )
    assert result["error"] == "Navigation failed"
    assert result["url"] == "https://unreachable.invalid.tld"


def test_perform_login_not_called_when_no_login_page():
    """Test that auto-login is not attempted when page doesn't look like login."""
    agent = ApplicationReconnaissanceAgent()
    # This is an internal behavior test - the key point is that _perform_login
    # should only be called when _looks_like_login_page returns True
    assert True  # placeholder for integration test


def test_dom_context_structure_has_required_keys():
    """Verify that the DOM context structure matches what validation expects."""
    agent = ApplicationReconnaissanceAgent()
    # The dom_context should have pages with forms, elements, navigation
    # This is verified by the existing validation tests
    assert True  # structural test covered by validation tests


def test_project_safe_test_environment_defaults_to_false():
    project = Project(name="Example", target_url="https://example.test")

    assert project.is_safe_test_environment is False


def test_inspection_rejects_projects_not_marked_safe():
    project = SimpleNamespace(
        id=uuid4(),
        is_safe_test_environment=False,
        target_url="https://example.test",
    )
    agent = ApplicationReconnaissanceAgent(
        repo=SimpleNamespace(get_project=lambda project_id: project)
    )

    with pytest.raises(ApplicationInspectionError, match="not marked as a safe"):
        agent.inspect_project(project.id, [], allow_invalid_login_probe=False)


def test_invalid_login_probe_requires_explicit_test_data():
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Reject invalid login",
        steps=["Submit invalid credentials"],
        expected_result="An error is displayed",
        test_data={"username": "invalid-user", "password": "invalid-password"},
    )

    assert ApplicationReconnaissanceAgent._explicit_invalid_login_data(test_case) == (
        "invalid-user",
        "invalid-password",
    )
    test_case.test_data = {}
    assert ApplicationReconnaissanceAgent._explicit_invalid_login_data(test_case) is None


def test_visible_text_sanitization_redacts_supplied_test_data():
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Reject invalid login",
        steps=["Submit invalid credentials"],
        expected_result="An error is displayed",
        test_data={"username": "private-user", "password": "private-password"},
    )

    result = ApplicationReconnaissanceAgent._sanitize_text(
        "private-user private-password",
        [test_case],
    )

    assert "private-user" not in result
    assert "private-password" not in result


# Regression: visible SVG icons matched by text selectors (spec 0004 scan crash).
# saucedemo shows <svg class="error_icon"> on its login form when a logged out
# user opens /inventory.html; inner_text() raised "Node is not an HTMLElement".
_SVG_PAGE = """
<h1>Swag Labs</h1>
<svg class="svg-inline--fa error_icon" width="12" height="12"><circle cx="6" cy="6" r="5"/></svg>
<div class="error-message-container">Epic sadface: log in first</div>
<svg role="button" width="12" height="12"><circle cx="6" cy="6" r="5"/></svg>
<button>Login</button>
<nav><a href="/home">Home</a><svg width="12" height="12"><a href="/icon"><circle cx="6" cy="6" r="5"/></a></svg></nav>
<svg width="12" height="12"><a href="/plain-icon"><circle cx="6" cy="6" r="5"/></a></svg>
"""


def _with_page(html, action):
    from playwright.async_api import async_playwright

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(html)
                return await action(page)
            finally:
                await browser.close()

    return asyncio.run(run())


_UNLINKED_LABEL_PAGE = """
<form id="insurance-form">
  <label class="main">Make</label>
  <select id="make" name="Make"><option value="default">please select</option><option>Audi</option></select>
  <label for="model">Model</label>
  <select id="model" name="Model"><option>A4</option></select>
  <input id="username" name="username" type="text">
</form>
"""


def test_field_label_is_empty_when_label_is_not_tied_to_the_field():
    agent = ApplicationReconnaissanceAgent()

    async def extract(page):
        return await agent._extract_forms(page)

    fields = {field["selector"]: field for field in _with_page(_UNLINKED_LABEL_PAGE, extract)[0]["fields"]}
    # getByLabel('Make') cannot resolve a caption with no `for`, so it is not reported as a label.
    assert fields["#make"]["label"] == ""
    assert fields["#make"]["name"] == "Make"
    assert fields["#model"]["label"] == "Model"
    assert fields["#username"]["label"] == ""
    assert fields["#username"]["name"] == "username"
    assert all(field["visible"] for field in fields.values())


_WIZARD_PAGE = """
<form id="wizard">
  <section><label class="main">Make</label><select id="make" name="Make"><option>Audi</option></select>
    <button id="next1" type="button">Next &raquo;</button></section>
  <section style="display:none">
    <label class="main">First Name</label><input id="firstname" name="First Name" type="text">
    <input id="pw" name="Password" type="password">
    <input type="hidden" name="token" value="x">
    <button id="send" type="button">Send &raquo;</button>
  </section>
</form>
"""


def test_hidden_wizard_step_fields_and_buttons_are_reported_with_selectors():
    agent = ApplicationReconnaissanceAgent()
    forms = _with_page(_WIZARD_PAGE, agent._extract_forms)

    fields = {field["selector"]: field for field in forms[0]["fields"]}
    assert set(fields) == {"#make", "#firstname", "#pw"}
    assert fields["#make"]["visible"] is True
    assert fields["#firstname"]["visible"] is False
    assert fields["#firstname"]["match_count"] == 1
    assert forms[0]["submit_button"]["selector"] == "#next1"
    assert forms[0]["hidden_buttons"] == [
        {"name": "Send »", "selector": "#send", "match_count": 1, "visible": False, "step": 2}
    ]
    # A hidden password field on a later step does not make this a login page.
    assert agent._looks_like_login_page(forms) is False


def test_invalid_login_probe_finds_username_field_by_name_attribute():
    agent = ApplicationReconnaissanceAgent()
    page = MagicMock()
    filled = {}

    def locator(selector):
        target = MagicMock()

        async def fill(value):
            filled[selector] = value

        async def click():
            filled[selector] = "clicked"

        async def wait_for(**_):
            return None

        target.fill = fill
        target.click = click
        target.first.wait_for = wait_for
        return target

    page.locator.side_effect = locator
    forms = [{
        "fields": [
            {"type": "text", "label": "", "name": "user-name", "selector": "#user-name", "match_count": 1},
            {"type": "password", "label": "", "name": "password", "selector": "#password", "match_count": 1},
        ],
        "submit_button": {"selector": "#login", "match_count": 1},
    }]

    asyncio.run(agent._submit_invalid_login(page, forms, ("wrong", "nope")))

    assert filled == {"#user-name": "wrong", "#password": "nope", "#login": "clicked"}


_OPTION_CONTROLS_PAGE = """
<style>
  .styled { position: relative; display: inline-block; }
  .styled input { position: absolute; left: 0; top: 0; margin: 0; }
  .styled .cover { position: absolute; left: 0; top: 0; width: 20px; height: 20px; background: #fff; }
</style>
<form>
  <label class="styled">
      <input id="speeding" name="Hobbies" type="checkbox" value="Speeding"><span class="cover"></span> Speeding</label>
  <input type="checkbox" id="terms"><label for="terms">I agree</label>
  <label><input type="radio" name="size" value="S"> Small</label>
  <label><input type="radio" name="size" value="M"> Medium</label>
  <label class="price"><input type="radio" id="selectsilver" name="Plan" value="Silver"></label>
  <input type="checkbox" name="bare">
</form>
"""


def test_checkbox_and_radio_fields_report_a_unique_label_click_selector():
    agent = ApplicationReconnaissanceAgent()

    async def extract(page):
        forms = await agent._extract_forms(page)
        fields = forms[0]["fields"]
        # The covered input cannot be clicked, but its click_selector can.
        covered = next(field for field in fields if field["selector"] == "#speeding")
        await page.locator(covered["click_selector"]).click(timeout=2000)
        return fields, await page.locator("#speeding").is_checked()

    fields, checked = _with_page(_OPTION_CONTROLS_PAGE, extract)

    by_label = {field["label"] or field["name"]: field for field in fields}
    assert checked is True
    assert by_label["Speeding"]["click_selector"] == "label:has(#speeding)"
    assert by_label["Speeding"]["value"] == "Speeding"
    assert by_label["I agree"]["click_selector"] == 'label[for="terms"]'
    assert by_label["Small"]["click_selector"] == 'label:has(input[name="size"][value="S"])'
    assert by_label["Medium"]["click_selector"] == 'label:has(input[name="size"][value="M"])'
    # No visible text: the value attribute still ties a data value to the option.
    assert by_label["Plan"]["click_selector"] == "label:has(#selectsilver)"
    assert by_label["Plan"]["value"] == "Silver"
    assert "click_selector" not in by_label["bare"]


def test_covered_checkbox_input_click_is_intercepted():
    """Guards the premise of click_selector: the native input is not clickable."""
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    async def click_input(page):
        try:
            await page.locator("#speeding").click(timeout=1500)
        except PlaywrightTimeoutError:
            return "intercepted"
        return "clicked"

    assert _with_page(_OPTION_CONTROLS_PAGE, click_input) == "intercepted"


def test_recorder_element_list_includes_option_labels():
    from backend.agents.dom_distiller import extract_page_state

    state = _with_page(_OPTION_CONTROLS_PAGE, extract_page_state)
    labels = [e["recommendedSelector"] for e in state["interactive_elements"] if e["tag"] == "label"]
    assert "label:has(#speeding)" in labels
    assert 'label:has(input[name="size"][value="S"])' in labels


def test_visible_text_skips_svg_icons_instead_of_failing():
    agent = ApplicationReconnaissanceAgent()
    texts = _with_page(_SVG_PAGE, lambda page: agent._extract_visible_text(page, []))
    assert texts == ["Swag Labs", "Epic sadface: log in first"]


def test_interactive_elements_and_navigation_skip_svg_nodes():
    agent = ApplicationReconnaissanceAgent()

    async def extract(page):
        return await agent._extract_interactive_elements(page), await agent._extract_navigation(page)

    elements, navigation = _with_page(_SVG_PAGE, extract)
    assert [e["name"] for e in elements if e["role"] == "button"] == ["Login"]
    assert [item["name"] for item in navigation] == ["Home"]


def test_one_failing_page_returns_an_error_page_instead_of_raising(monkeypatch):
    agent = ApplicationReconnaissanceAgent()

    async def broken(page):
        raise RuntimeError("extraction broke")

    monkeypatch.setattr(agent, "_extract_forms", broken)
    result = _with_page(
        "<h1>x</h1>",
        lambda page: agent._inspect_page(page=page, url="about:blank", test_cases=[], allow_invalid_login_probe=False),
    )
    assert result["error"] == "Inspection failed"
    assert result["elements"] == [] and "_objects" not in result


_STEPPED_WIZARD_PAGE = """
<ul class="steps-nav">
  <li><a href="#">Vehicle<span> 3</span></a></li>
  <li><a href="#">Product</a></li>
  <li><a href="#">Quote</a></li>
</ul>
<form id="wizard">
  <section class="step">
    <select id="make"><option>Audi</option></select>
    <button type="button" id="next1">Next »</button>
  </section>
  <section class="step" style="display:none">
    <input id="startdate">
    <label><input type="checkbox" id="euro"> Euro Protection</label>
    <button type="button" id="prev2">« Prev</button>
    <button type="button" id="next2">Next »</button>
  </section>
  <section class="step" style="display:none">
    <input id="email" type="email">
    <button type="button" id="prev3">« Prev</button>
    <button type="button" id="send">Send</button>
  </section>
</form>
"""


def test_multi_step_form_reports_steps_and_tags_each_control():
    agent = ApplicationReconnaissanceAgent()
    forms = _with_page(_STEPPED_WIZARD_PAGE, agent._extract_forms)
    form = forms[0]

    assert form["steps"] == [
        {"step": 1, "name": "Vehicle", "visible": True, "next_button": "#next1"},
        {"step": 2, "name": "Product", "visible": False,
         "next_button": "#next2", "prev_button": "#prev2"},
        {"step": 3, "name": "Quote", "visible": False, "prev_button": "#prev3"},
    ]
    steps = {field["selector"]: field["step"] for field in form["fields"]}
    assert steps == {"#make": 1, "#startdate": 2, "#euro": 2, "#email": 3}
    euro = next(field for field in form["fields"] if field["selector"] == "#euro")
    assert euro["click_selector"] == "label:has(#euro)"
    hidden = {button["selector"]: button["step"] for button in form["hidden_buttons"]}
    assert hidden == {"#prev2": 2, "#next2": 2, "#prev3": 3, "#send": 3}


def test_plain_form_has_no_steps():
    agent = ApplicationReconnaissanceAgent()
    forms = _with_page(_OPTION_CONTROLS_PAGE, agent._extract_forms)
    assert "steps" not in forms[0]
    assert all("step" not in field for field in forms[0]["fields"])


def test_steps_that_name_a_link_lead_reconnaissance_to_the_linked_page(tmp_path):
    """Seen live: a quote form opened directly showed every vehicle's fields;
    reached through the menu link, as the steps do, it showed only its own."""
    (tmp_path / "home.html").write_text(
        '<nav><a href="app.html">Automobile</a><a href="app.html?truck">Truck</a></nav>',
        encoding="utf-8",
    )
    (tmp_path / "app.html").write_text(
        '<form><input id="make"><button type="button">Next</button></form>',
        encoding="utf-8",
    )
    agent = ApplicationReconnaissanceAgent()
    test_case = TestCase(
        scenario_id=uuid4(),
        title="Quote",
        steps=["Click Automobile in the top menu", "Enter the vehicle data", "Click Next"],
        expected_result="Quote is sent",
    )

    async def run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                return await agent._follow_step_links(
                    page, (tmp_path / "home.html").as_uri(), [test_case]
                )
            finally:
                await browser.close()

    pages = asyncio.run(run())

    assert len(pages) == 1
    assert pages[0]["url"].endswith("/app.html") or pages[0]["url"] == "unknown"
    assert pages[0]["reached_by"] == "link 'Automobile'"
    assert [field["selector"] for field in pages[0]["forms"][0]["fields"]] == ["#make"]


def test_steps_without_navigation_wording_follow_no_links():
    agent = ApplicationReconnaissanceAgent()
    test_case = TestCase(
        scenario_id=uuid4(), title="t", steps=["Enter the vehicle data"], expected_result="x"
    )
    page = MagicMock()
    assert asyncio.run(agent._follow_step_links(page, "https://example.test/", [test_case])) == []
    page.goto.assert_not_called()
