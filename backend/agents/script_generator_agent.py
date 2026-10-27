"""Live-browser test script generator agent.

Executes an approved test case's steps against the project's target URL in
a headless Python Playwright browser, verifies every locator on the live
DOM, and synthesizes a TypeScript @playwright/test (.spec.ts) script that
uses only the verified selectors.

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

from backend.services.llm import DEFAULT_LLM_TIMEOUT, LLMService

from .dom_distiller import extract_page_state

logger = logging.getLogger(__name__)


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
    selector: str = Field(
        description="Valid Playwright selector string passed to page.locator(selector)"
    )
    value: Optional[str] = Field(
        default=None,
        description="Text to fill, option value to select, key to press, or expected text",
    )
    ts_playwright_code: str = Field(
        description="Equivalent TypeScript @playwright/test line of code for this action"
    )
    reasoning: str


async def execute_live_playwright_action(
    page: Page, action: str, selector: str, value: Optional[str] = None
) -> None:
    """Executes the decided action on the live Python Playwright page to verify the locator works."""
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


async def generate_accurate_test_script(
    target_url: str,
    steps: List[Union[str, Dict[str, Any]]],
    test_title: str = "Generated End-to-End Test",
    llm: Optional[LLMService] = None,
) -> str:
    """
    Executes test steps against target_url in a live Python Playwright browser,
    verifies all selectors, and outputs a TypeScript @playwright/test (.spec.ts) script.
    """
    if not target_url:
        raise ValueError("target_url is required for live script generation")
    if not steps:
        raise ValueError("At least one test step is required for live script generation")

    llm = llm or LLMService()
    headless = os.environ.get("HEADLESS_AGENT", "true").lower() != "false"
    recorded_history: List[Dict[str, Any]] = []
    logger.info(
        "ScriptGeneratorAgent started: target_url=%s steps=%s", target_url, len(steps)
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
                last_error = ""

                while not step_completed and sub_attempts < 6:
                    sub_attempts += 1
                    page_state = await extract_page_state(page)
                    aria_snapshot = page_state["aria_snapshot"][:5000]
                    interactive_elements = page_state["interactive_elements"][:120]

                    prompt = f"""
You are an autonomous Playwright QA Execution Agent.
Your job is to execute the Current Step on the live browser and record the equivalent TypeScript @playwright/test code.

Test Title: "{test_title}"
Current URL: {page.url}
Current Step (#{idx}): {step_description}

Recent Verified Actions:
{json.dumps(recorded_history[-5:], indent=2)}

{f"LAST ACTION FAILED WITH ERROR (Pick a different selector from Live Interactive Elements!): {last_error}" if last_error else ""}

Live ARIA Snapshot:
{aria_snapshot}

Live Interactive Elements:
{json.dumps(interactive_elements, indent=2)}

Rules:
1. If Current Step (including its expected verification) is completely satisfied on the page, return action="done".
2. Otherwise, return the NEXT single atomic Playwright action using a verified selector from Live Interactive Elements.
3. Prefer [data-testid], [data-test], #id, or unique attribute selectors.
4. For multi-box PIN/OTP inputs (e.g., 5 separate inputs with maxlength="1"), fill each box sequentially.
5. `ts_playwright_code` MUST be valid TypeScript @playwright/test syntax (e.g. `await page.locator('...').click();`).
"""

                    # LLMService is synchronous; keep the event loop free for the browser.
                    decision = await asyncio.to_thread(
                        llm.structured_generate,
                        user=prompt,
                        response_model=AgentDecision,
                        temperature=0.1,
                    )

                    if decision.action == "done":
                        step_completed = True
                        break

                    try:
                        await execute_live_playwright_action(
                            page, decision.action, decision.selector, decision.value
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

                if not step_completed:
                    logger.warning(
                        "ScriptGeneratorAgent step %s not confirmed done after %s attempts",
                        idx,
                        sub_attempts,
                    )

        finally:
            await browser.close()

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
"""

    final_text = await asyncio.to_thread(
        llm.generate,
        system="You are a senior QA automation engineer who writes TypeScript @playwright/test scripts.",
        user=synthesis_prompt,
        timeout=DEFAULT_LLM_TIMEOUT,
        temperature=0.1,
    )

    clean_ts_code = re.sub(
        r"^```(?:typescript|ts)?\n?|```$", "", (final_text or "").strip(), flags=re.IGNORECASE | re.MULTILINE
    ).strip()

    if not clean_ts_code:
        raise ValueError("ScriptGeneratorAgent produced an empty TypeScript script")

    logger.info(
        "ScriptGeneratorAgent completed: verified_actions=%s", len(recorded_history)
    )
    return clean_ts_code
