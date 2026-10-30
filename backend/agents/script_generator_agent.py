"""Live-browser test script generator agent.

Executes an approved test case's steps against the project's target URL in
a headless Python Playwright browser, verifies every locator on the live
DOM, and synthesizes a TypeScript @playwright/test (.spec.ts) script that
uses only the verified selectors.

generate_data_driven_template records the same way with one dataset's
values, then writes a template with data markers that PlaywrightAgent runs
once per dataset.

LLM calls go through LLMService; pass ``llm`` to use a test double.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Literal, Optional, Union

from playwright.async_api import Page, async_playwright
from pydantic import BaseModel, Field

from backend.services.llm import DEFAULT_LLM_TIMEOUT, LLMService, LLMServiceError

from .dom_distiller import extract_page_state

logger = logging.getLogger(__name__)

# Each step call carries page context, so slow and reasoning models need more
# than LLMService's 30s default.
STEP_LLM_TIMEOUT = 120
MAX_ARIA_CHARS = 3000
MAX_INTERACTIVE_ELEMENTS = 80

# The LLM only ever sees these placeholders; real credentials are swapped in
# at fill time and never enter a prompt, the trace, or the generated script.
USERNAME_PLACEHOLDER = "{{TARGET_USERNAME}}"
PASSWORD_PLACEHOLDER = "{{TARGET_PASSWORD}}"


class AgentDecision(BaseModel):
    action: Literal[
        "click",
        "fill",
        "select_option",
        "press",
        "assert_visible",
        "assert_text",
        "done",
    ]
    selector: Optional[str] = Field(
        default=None,
        description="Valid Playwright selector string passed to page.locator(selector); omit for done",
    )
    value: Optional[str] = Field(
        default=None,
        description="Text to fill, option value to select, key to press, or expected text",
    )
    ts_playwright_code: str = Field(
        default="",
        description="Equivalent TypeScript @playwright/test line of code for this action",
    )
    reasoning: str = ""


def _compact_elements(elements: List[Dict[str, Any]]) -> str:
    """One compact JSON line per element, without empty fields or the DOM index."""
    lines = []
    for element in elements[:MAX_INTERACTIVE_ELEMENTS]:
        kept = {k: v for k, v in element.items() if k != "index" and v not in (None, "")}
        lines.append(json.dumps(kept, separators=(",", ":")))
    return "\n".join(lines)


def _resolve_credential_placeholders(
    value: Optional[str], username: Optional[str], password: Optional[str]
) -> Optional[str]:
    if not value:
        return value
    if username:
        value = value.replace(USERNAME_PLACEHOLDER, username)
    if password:
        value = value.replace(PASSWORD_PLACEHOLDER, password)
    return value


async def execute_live_playwright_action(
    page: Page, action: str, selector: Optional[str], value: Optional[str] = None
) -> None:
    """Executes the decided action on the live Python Playwright page to verify the locator works."""
    if not selector:
        raise ValueError(f'Action "{action}" needs a selector from Live Interactive Elements')
    locator = page.locator(selector).first

    if action == "click":
        await locator.click(timeout=5000)
    elif action == "fill":
        await locator.fill(value or "", timeout=5000)
    elif action == "select_option":
        await locator.select_option(value or "", timeout=5000)
    elif action == "press":
        await locator.press(value or "Enter", timeout=5000)
    elif action == "assert_visible":
        await locator.wait_for(state="visible", timeout=5000)
    elif action == "assert_text":
        await locator.wait_for(state="visible", timeout=5000)
        actual_text = await locator.inner_text()
        if value and value not in actual_text:
            raise AssertionError(
                f'Expected "{selector}" to contain "{value}", but got "{actual_text}"'
            )
    else:
        raise ValueError(f"Unsupported action: {action}")


def _credentials_script_rule(number: int, has_credentials: bool) -> str:
    if not has_credentials:
        return ""
    return (
        f"{number}. Write {USERNAME_PLACEHOLDER} as process.env.TARGET_USERNAME and "
        f"{PASSWORD_PLACEHOLDER} as process.env.TARGET_PASSWORD, with no fallback values."
    )


def _clean_ts_code(text: Optional[str]) -> str:
    clean_ts_code = re.sub(
        r"^```(?:typescript|ts)?\n?|```$", "", (text or "").strip(), flags=re.IGNORECASE | re.MULTILINE
    ).strip()
    if not clean_ts_code:
        raise ValueError("ScriptGeneratorAgent produced an empty TypeScript script")
    return clean_ts_code


async def generate_accurate_test_script(
    target_url: str,
    steps: List[Union[str, Dict[str, Any]]],
    test_title: str = "Generated End-to-End Test",
    llm: Optional[LLMService] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    Executes test steps against target_url in a live Python Playwright browser,
    verifies all selectors, and outputs a TypeScript @playwright/test (.spec.ts) script.

    username and password come from the project vault. The LLM sees only
    placeholders for them; they are substituted when a fill runs.
    """
    if not target_url:
        raise ValueError("target_url is required for live script generation")
    if not steps:
        raise ValueError("At least one test step is required for live script generation")

    llm = llm or LLMService()
    has_credentials = bool(username or password)
    logger.info(
        "ScriptGeneratorAgent started: target_url=%s steps=%s", target_url, len(steps)
    )
    recorded_history = await _record_verified_trace(
        target_url, steps, test_title, llm, username, password
    )

    # Final Phase: Synthesize the complete TypeScript @playwright/test (.spec.ts) file
    synthesis_prompt = f"""
Convert this live-verified Playwright execution trace into a clean, complete, production-ready TypeScript Playwright test script (`@playwright/test`).

Target URL: {target_url}
Test Title: {test_title}
Original Input Steps:
{json.dumps(steps, indent=2)}

Live Verified Execution Trace (Selectors proven on live DOM):
{json.dumps(recorded_history, indent=2)}

Strict Requirements:
1. Output MUST be written in TypeScript using `import {{ test, expect }} from '@playwright/test';`.
2. Use ONLY the verified selectors from `Live Verified Execution Trace`. Never invent or hallucinate selectors.
3. Group the code with clear step comments (`// --- Step 1: ... ---`).
4. Include proper Playwright Web-First Assertions (`await expect(...).toBeVisible()`, `await expect(...).toHaveText(...)`, `await expect(page).toHaveURL(...)`) for all verification steps, plus dynamic sorting/math checks if required by a step.
5. Return ONLY valid TypeScript code with no markdown fences or explanations.
{_credentials_script_rule(6, has_credentials)}
"""

    final_text = await asyncio.to_thread(
        llm.generate,
        system="You are a senior QA automation engineer who writes TypeScript @playwright/test scripts.",
        user=synthesis_prompt,
        timeout=DEFAULT_LLM_TIMEOUT,
        temperature=0.1,
    )
    clean_ts_code = _clean_ts_code(final_text)

    logger.info(
        "ScriptGeneratorAgent completed: verified_actions=%s", len(recorded_history)
    )
    return clean_ts_code


async def generate_data_driven_template(
    target_url: str,
    steps: List[str],
    test_title: str,
    expected_result: str,
    sample_data: Dict[str, str],
    markers: Dict[str, str],
    preconditions: Optional[List[str]] = None,
    repair_feedback: Optional[str] = None,
    llm: Optional[LLMService] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> str:
    """
    Records one dataset's run on the live target app, then writes a reusable
    @playwright/test template from the verified trace.

    The steps run with sample_data's values. The template uses each field's
    marker (from markers) in place of its value, so PlaywrightAgent can run
    the one recorded test once per dataset. test_title must already contain
    the dataset id and type markers.
    """
    if not target_url:
        raise ValueError("target_url is required for live template recording")
    if not steps:
        raise ValueError("At least one test step is required for live template recording")

    llm = llm or LLMService()
    has_credentials = bool(username or password)
    run_steps = [
        *(f"Make sure this precondition holds: {item}" for item in preconditions or []),
        *steps,
        f"Verify the expected result: {expected_result}",
    ]
    run_context = (
        "Test Data for this run (type these exact values wherever a step enters data): "
        + json.dumps(sample_data, ensure_ascii=False)
    )
    logger.info(
        "ScriptGeneratorAgent template recording started: target_url=%s steps=%s",
        target_url,
        len(run_steps),
    )
    recorded_history = await _record_verified_trace(
        target_url, run_steps, test_title, llm, username, password, run_context
    )

    field_lines = "\n".join(
        f"- {field}: recorded value {json.dumps(value, ensure_ascii=False)} "
        f"is replaced by '{markers[field]}'"
        for field, value in sample_data.items()
    )
    synthesis_prompt = f"""
Convert this live-verified Playwright execution trace into a reusable, data-driven TypeScript `@playwright/test` template. The platform runs the template once per dataset and swaps each marker for that dataset's value.

Target URL: {target_url}
Test Title (use verbatim, including its markers): {test_title}
Expected Result: {expected_result}
Steps:
{json.dumps(run_steps, indent=2, ensure_ascii=False)}

Recorded test data and the marker that replaces each value:
{field_lines}

Live Verified Execution Trace (selectors proven on the live DOM, recorded with the values above):
{json.dumps(recorded_history, indent=2, ensure_ascii=False)}

Strict Requirements:
1. Import with `import {{ test, expect }} from '@playwright/test';`, declare `const BASE_URL = process.env.TARGET_URL || {json.dumps(target_url)};`, and start the test with `await page.goto(BASE_URL);`.
2. Write exactly ONE test(...) block, titled with the Test Title verbatim.
3. Use ONLY the verified selectors from the trace. Never invent or hallucinate selectors.
4. Wherever the trace typed, selected, or checked a recorded test data value, use that field's marker as a string literal instead, for example fill('__TEST_DATA_01__'). Every marker listed above must appear in the script. A marker may hold an empty string; still apply it.
5. When the trace typed a value one character per input box (such as a PIN or OTP), loop over the marker's characters and fill box i with character i, for example `const boxes = [<the verified box selectors in order>]; for (const [i, ch] of [...'__TEST_DATA_01__'].entries()) {{ await page.locator(boxes[i]).fill(ch); }}`.
6. Assert the Expected Result with web-first assertions (`await expect(...).toBeVisible()`, `toHaveText(...)`, `await expect(page).toHaveURL(...)`), based on what the trace verified. Never branch on marker values.
7. Group the code with step comments (`// --- Step 1: ... ---`).
8. Return ONLY valid TypeScript code with no markdown fences or explanations.
{_credentials_script_rule(9, has_credentials)}
{f"REPAIR FEEDBACK (fix these problems from the previous script): {repair_feedback[:2000]}" if repair_feedback else ""}
"""

    final_text = await asyncio.to_thread(
        llm.generate,
        system="You are a senior QA automation engineer who writes TypeScript @playwright/test scripts.",
        user=synthesis_prompt,
        timeout=DEFAULT_LLM_TIMEOUT,
        temperature=0.1,
    )
    clean_ts_code = _clean_ts_code(final_text)

    logger.info(
        "ScriptGeneratorAgent template recording completed: verified_actions=%s",
        len(recorded_history),
    )
    return clean_ts_code


async def _record_verified_trace(
    target_url: str,
    steps: List[Union[str, Dict[str, Any]]],
    test_title: str,
    llm: LLMService,
    username: Optional[str],
    password: Optional[str],
    run_context: str = "",
) -> List[Dict[str, Any]]:
    """Runs each step on the live page and returns the actions that worked."""
    headless = os.environ.get("HEADLESS_AGENT", "true").lower() != "false"
    recorded_history: List[Dict[str, Any]] = []
    has_credentials = bool(username or password)
    credentials_rule = (
        f"6. For the project login, set value to {USERNAME_PLACEHOLDER} or {PASSWORD_PLACEHOLDER} "
        "(never a real or invented credential) and use process.env.TARGET_USERNAME or "
        "process.env.TARGET_PASSWORD in `ts_playwright_code`. For an invalid login, use only "
        "values from the step's Test Data.\n"
        if has_credentials
        else ""
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)

            for idx, raw_step in enumerate(steps, start=1):
                if isinstance(raw_step, str):
                    step_description = raw_step
                else:
                    action_txt = raw_step.get("action") or raw_step.get("step") or ""
                    data_txt = raw_step.get("test_data") or raw_step.get("testData") or "N/A"
                    exp_txt = raw_step.get("expected_result") or raw_step.get("expectedResult") or "N/A"
                    step_description = f"Action: {action_txt} | Test Data: {data_txt} | Expected: {exp_txt}"

                step_completed = False
                sub_attempts = 0
                llm_failures = 0
                last_error = ""
                last_llm_error: Optional[LLMServiceError] = None

                while not step_completed and sub_attempts < 6:
                    sub_attempts += 1
                    page_state = await extract_page_state(page)
                    aria_snapshot = page_state["aria_snapshot"][:MAX_ARIA_CHARS]
                    interactive_elements = _compact_elements(page_state["interactive_elements"])

                    prompt = f"""
You are an autonomous Playwright QA Execution Agent.
Your job is to execute the Current Step on the live browser and record the equivalent TypeScript @playwright/test code.

Test Title: "{test_title}"
Current URL: {page.url}
Current Step (#{idx}): {step_description}
{run_context}

Recent Verified Actions:
{json.dumps(recorded_history[-5:], indent=2)}

{f"LAST ACTION FAILED WITH ERROR (Pick a different selector from Live Interactive Elements!): {last_error}" if last_error else ""}

Live ARIA Snapshot:
{aria_snapshot}

Live Interactive Elements (one JSON object per line):
{interactive_elements}

Rules:
1. If Current Step (including its expected verification) is completely satisfied on the page, return action="done" (selector may be omitted).
2. Otherwise, return the NEXT single atomic Playwright action using a verified selector from Live Interactive Elements.
3. Prefer [data-testid], [data-test], #id, or unique attribute selectors.
4. For multi-box PIN/OTP inputs (e.g., 5 separate inputs with maxlength="1"), fill each box sequentially.
5. `ts_playwright_code` MUST be valid TypeScript @playwright/test syntax (e.g. `await page.locator('...').click();`).
{credentials_rule}"""

                    # LLMService is synchronous; keep the event loop free for the browser.
                    try:
                        decision = await asyncio.to_thread(
                            llm.structured_generate,
                            user=prompt,
                            response_model=AgentDecision,
                            timeout=STEP_LLM_TIMEOUT,
                            temperature=0.1,
                        )
                    except LLMServiceError as err:
                        # One bad response should not end the whole script; try the step again.
                        llm_failures += 1
                        last_llm_error = err
                        logger.warning(
                            "ScriptGeneratorAgent LLM call failed: step=%s attempt=%s error=%s",
                            idx,
                            sub_attempts,
                            err,
                        )
                        continue

                    if decision.action == "done":
                        step_completed = True
                        break

                    action_value = decision.value
                    if decision.action == "fill":
                        action_value = _resolve_credential_placeholders(
                            decision.value, username, password
                        )

                    try:
                        await execute_live_playwright_action(
                            page, decision.action, decision.selector, action_value
                        )
                        await page.wait_for_timeout(600)  # Allow SPA state transitions

                        recorded_history.append(
                            {
                                "step_number": idx,
                                "step_goal": step_description,
                                "url": page.url,
                                "action": decision.action,
                                "selector": decision.selector,
                                "value": decision.value,
                                "ts_playwright_code": decision.ts_playwright_code,
                            }
                        )
                        last_error = ""
                    except Exception as err:
                        last_error = str(err)
                        logger.info(
                            "ScriptGeneratorAgent action failed, retrying: step=%s attempt=%s selector=%s",
                            idx,
                            sub_attempts,
                            decision.selector,
                        )

                if llm_failures == sub_attempts and last_llm_error is not None:
                    # Every attempt failed at the provider, so a script built from
                    # this trace would silently miss the step. Surface the real error.
                    raise LLMServiceError(
                        f"Script generation stopped at step {idx}: every LLM call failed. "
                        f"Last error: {last_llm_error}"
                    ) from last_llm_error

                if not step_completed:
                    logger.warning(
                        "ScriptGeneratorAgent step %s not confirmed done after %s attempts",
                        idx,
                        sub_attempts,
                    )

        finally:
            await browser.close()

    return recorded_history
