import asyncio
import unittest.mock as mock

import pytest

from backend.agents import script_generator_agent as agent
from backend.agents.script_generator_agent import AgentDecision, generate_accurate_test_script
from backend.services.llm import LLMServiceError


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
    """Returns scripted decisions in order; an Exception entry is raised instead."""

    def __init__(self, decisions, final_text):
        self.decisions = list(decisions)
        self.final_text = final_text
        self.prompts = []
        self.timeouts = []
        self.synthesis_prompt = None

    def structured_generate(self, *, user, response_model, **kwargs):
        self.prompts.append(user)
        self.timeouts.append(kwargs.get("timeout"))
        decision = self.decisions.pop(0)
        if isinstance(decision, Exception):
            raise decision
        return response_model(**decision)

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


def _run(llm, steps, **kwargs):
    page = _FakePage()
    browser = _FakeBrowser(page)
    with mock.patch.object(agent, "async_playwright", lambda: _FakePlaywright(browser)), \
         mock.patch.object(agent, "extract_page_state", _fake_page_state):
        script = asyncio.run(
            generate_accurate_test_script(
                "https://app.example.test/", steps, "Login", llm=llm, **kwargs
            )
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


def test_agent_decision_accepts_done_without_selector():
    # Many models send null or omit selector when a step is finished.
    assert AgentDecision(action="done", selector=None).selector is None
    assert AgentDecision(action="done").ts_playwright_code == ""


def test_action_without_selector_is_fed_back_as_error():
    llm = _ScriptedLLM(
        [{"action": "click", "selector": None}, _decision("click", "#login"), _decision("done")],
        "import { test } from '@playwright/test';",
    )

    _, page, _ = _run(llm, ["Click login"])

    assert page.actions == [("click", "#login", None)]
    assert 'Action "click" needs a selector' in llm.prompts[1]


def test_step_calls_use_longer_timeout_and_compact_context():
    llm = _ScriptedLLM([_decision("done")], "import { test } from '@playwright/test';")

    _run(llm, ["Open page"])

    assert llm.timeouts == [agent.STEP_LLM_TIMEOUT]
    assert '{"recommendedSelector":"#login"}' in llm.prompts[0]


def test_single_llm_failure_retries_the_step():
    llm = _ScriptedLLM(
        [LLMServiceError("rate limited"), _decision("click", "#login"), _decision("done")],
        "import { test } from '@playwright/test';",
    )

    script, page, browser = _run(llm, ["Click login"])

    assert page.actions == [("click", "#login", None)]
    assert script.startswith("import")
    assert browser.closed


def test_step_raises_when_every_llm_call_fails():
    llm = _ScriptedLLM([LLMServiceError("Unauthorized")] * 6, "unused")

    with pytest.raises(LLMServiceError, match="step 1: every LLM call failed.*Unauthorized"):
        _run(llm, ["Click login"])
    assert llm.synthesis_prompt is None


def test_credentials_are_filled_live_but_never_sent_to_the_llm():
    llm = _ScriptedLLM(
        [
            _decision("fill", '[data-test="username"]', agent.USERNAME_PLACEHOLDER),
            _decision("done"),
        ],
        "import { test } from '@playwright/test';",
    )

    _, page, _ = _run(llm, ["Enter username"], username="real-user", password="s3cret!")

    assert page.actions == [("fill", '[data-test="username"]', "real-user")]
    assert agent.USERNAME_PLACEHOLDER in llm.prompts[0]
    assert "process.env.TARGET_USERNAME" in llm.synthesis_prompt
    for prompt in llm.prompts + [llm.synthesis_prompt]:
        assert "real-user" not in prompt
        assert "s3cret!" not in prompt


def test_no_credential_rule_without_credentials():
    llm = _ScriptedLLM([_decision("done")], "import { test } from '@playwright/test';")

    _run(llm, ["Open page"])

    assert agent.USERNAME_PLACEHOLDER not in llm.prompts[0]
    assert agent.USERNAME_PLACEHOLDER not in llm.synthesis_prompt


def test_data_driven_template_records_sample_values_and_asks_for_markers():
    llm = _ScriptedLLM(
        [
            _decision("fill", '[data-test="username"]', "locked_user"),
            _decision("done"),
            _decision("done"),
        ],
        "import { test, expect } from '@playwright/test';\ntest('__TEST_DATA_ID__', async () => {});",
    )
    page = _FakePage()
    browser = _FakeBrowser(page)

    with mock.patch.object(agent, "async_playwright", lambda: _FakePlaywright(browser)), \
         mock.patch.object(agent, "extract_page_state", _fake_page_state):
        script = asyncio.run(
            agent.generate_data_driven_template(
                "https://app.example.test/",
                ["Enter the username 'locked_user'"],
                "__TEST_DATA_ID__ [__TEST_TYPE__] Locked user is rejected",
                "The message 'User is locked' is shown",
                sample_data={"username": "locked_user"},
                markers={"username": "__TEST_DATA_01__"},
                llm=llm,
            )
        )

    assert script.startswith("import { test, expect }")
    # The steps run live with the sample values, then the expected result is verified live.
    assert page.actions == [("fill", '[data-test="username"]', "locked_user")]
    assert 'Test Data for this run' in llm.prompts[0]
    assert '"username": "locked_user"' in llm.prompts[0]
    assert "Verify the expected result: The message 'User is locked' is shown" in llm.prompts[2]
    assert browser.closed
    # The synthesis asks for the marker in place of the recorded value.
    assert "Test Title (use verbatim, including its markers): __TEST_DATA_ID__ [__TEST_TYPE__]" in llm.synthesis_prompt
    assert "username: recorded value \"locked_user\" is replaced by '__TEST_DATA_01__'" in llm.synthesis_prompt
    assert '[data-test=\\"username\\"]' in llm.synthesis_prompt
