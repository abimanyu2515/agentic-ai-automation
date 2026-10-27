import asyncio
import unittest.mock as mock

import pytest

from backend.agents import script_generator_agent as agent
from backend.agents.script_generator_agent import AgentDecision, generate_accurate_test_script


class _FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def fill(self, value, timeout):
        if self.selector not in self.page.valid_selectors:
            raise TimeoutError(f"Timeout waiting for {self.selector}")
        self.page.actions.append(("fill", self.selector, value))

    async def click(self, timeout):
        if self.selector not in self.page.valid_selectors:
            raise TimeoutError(f"Timeout waiting for {self.selector}")
        self.page.actions.append(("click", self.selector, None))


class _FakePage:
    url = "https://app.example.test/"

    def __init__(self):
        self.valid_selectors = {'[data-test="username"]', "#login"}
        self.actions = []
        self.visited = None

    async def goto(self, url, **kwargs):
        self.visited = url

    async def wait_for_timeout(self, ms):
        return None

    def locator(self, selector):
        return _FakeLocator(self, selector)


class _FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.closed = False

    async def new_context(self):
        return self

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = self
        self.browser = browser

    async def launch(self, headless):
        return self.browser

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _ScriptedLLM:
    def __init__(self, decisions, final_text):
        self.decisions = list(decisions)
        self.final_text = final_text
        self.prompts = []
        self.synthesis_prompt = None

    def structured_generate(self, *, user, response_model, **kwargs):
        self.prompts.append(user)
        return response_model(**self.decisions.pop(0))

    def generate(self, *, system, user, **kwargs):
        self.synthesis_prompt = user
        return self.final_text


async def _fake_page_state(page):
    return {"aria_snapshot": "- textbox", "interactive_elements": [{"recommendedSelector": "#login"}]}


def _decision(action, selector="", value=None):
    return {
        "action": action,
        "selector": selector,
        "value": value,
        "ts_playwright_code": f"await page.locator('{selector}').{action}();",
        "reasoning": "test",
    }


def _run(llm, steps):
    page = _FakePage()
    browser = _FakeBrowser(page)
    with mock.patch.object(agent, "async_playwright", lambda: _FakePlaywright(browser)), \
         mock.patch.object(agent, "extract_page_state", _fake_page_state):
        script = asyncio.run(
            generate_accurate_test_script("https://app.example.test/", steps, "Login", llm=llm)
        )
    return script, page, browser


def test_records_only_verified_actions_and_self_heals_failed_locator():
    llm = _ScriptedLLM(
        [
            _decision("fill", '[data-test="username"]', "standard_user"),
            _decision("done"),
            _decision("click", "#missing"),
            _decision("click", "#login"),
            _decision("done"),
        ],
        "```typescript\nimport { test, expect } from '@playwright/test';\n```",
    )

    script, page, browser = _run(llm, ["Enter username", {"action": "Click login"}])

    assert script == "import { test, expect } from '@playwright/test';"
    assert page.visited == "https://app.example.test/"
    assert page.actions == [
        ("fill", '[data-test="username"]', "standard_user"),
        ("click", "#login", None),
    ]
    assert browser.closed
    # The failed locator's error is fed back on the retry.
    assert "LAST ACTION FAILED WITH ERROR" in llm.prompts[3]
    assert "Timeout waiting for #missing" in llm.prompts[3]
    # Only verified selectors reach the synthesis trace.
    assert "#missing" not in llm.synthesis_prompt
    assert "#login" in llm.synthesis_prompt
    assert "Action: Click login | Test Data: N/A | Expected: N/A" in llm.prompts[2]


def test_step_gives_up_after_six_micro_actions():
    llm = _ScriptedLLM([_decision("click", "#missing")] * 6, "import { test } from '@playwright/test';")

    _, page, browser = _run(llm, ["Click something that is not there"])

    assert len(llm.prompts) == 6
    assert page.actions == []
    assert browser.closed


def test_browser_closes_when_llm_fails():
    class _FailingLLM:
        def structured_generate(self, **kwargs):
            raise RuntimeError("LLM down")

    browser = _FakeBrowser(_FakePage())
    with mock.patch.object(agent, "async_playwright", lambda: _FakePlaywright(browser)), \
         mock.patch.object(agent, "extract_page_state", _fake_page_state), \
         pytest.raises(RuntimeError, match="LLM down"):
        asyncio.run(
            generate_accurate_test_script("https://app.example.test/", ["Open page"], llm=_FailingLLM())
        )
    assert browser.closed


def test_requires_target_url_and_steps():
    with pytest.raises(ValueError, match="target_url"):
        asyncio.run(generate_accurate_test_script("", ["step"], llm=object()))
    with pytest.raises(ValueError, match="step"):
        asyncio.run(generate_accurate_test_script("https://x.test", [], llm=object()))


def test_agent_decision_rejects_unknown_action():
    with pytest.raises(Exception):
        AgentDecision(**_decision("hover", "#x"))
