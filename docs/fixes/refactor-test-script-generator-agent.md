# Task: Implement Live-Browser Playwright Test Script Generation Agent (Python Backend -> TypeScript Test Output)

## 1. Objective
Implement the **Test Script Generation Agent** in **Python** (triggered when the user provides a `target_url` and clicks **"Generate Script"** on an existing test case), which outputs a verified **TypeScript Playwright (`@playwright/test`)** `.spec.ts` test script.

**STRICT SCOPE & TECH STACK:**
- **Agent Implementation Language:** **Python** (using `playwright.async_api` or `playwright.sync_api` and our existing Python LLM client).
- **Generated Output Artifact:** **TypeScript Playwright (`@playwright/test`)** `.spec.ts` code.
- **DO NOT** create or modify any Scenario Generation or Test Case Generation agents. Those already exist in the Python project.
- **ONLY** implement/upgrade the **Test Script Generation Agent** so that it takes the existing approved Test Case + `target_url`, opens a live headless Playwright browser in Python, executes each step on the real DOM to discover and verify accurate locators, and synthesizes a working **TypeScript** `@playwright/test` script.


---

## 2. Architecture to Implement (Python Agent -> TypeScript Output)

Replace static prompt-only script generation with a 3-part **Live Browser Execution & Synthesis Engine** in Python:

### Part 1: Live DOM Distiller (`dom_distiller.py`)
Create an async helper `extract_page_state(page: Page) -> dict` that extracts only actionable DOM state from the live Playwright `page` (never send raw `await page.content()` HTML):
1. **Accessibility Snapshot:** Call `await page.locator("body").aria_snapshot()` (fallback gracefully if on an older Playwright version).
2. **Visible Interactive Elements:** Run `await page.evaluate(...)` to query visible interactive elements (`a, button, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [data-testid], [data-test], h1, h2, h3`) and compute a `recommended_selector` using strict priority:
   - `[data-testid="..."]` or `[data-test="..."]`
   - `#id` (non-dynamic)
   - `tag[name="..."]` or `tag[placeholder="..."]` or `tag[aria-label="..."]`
   - `button:has-text("...")` or `a:has-text("...")`

### Part 2: Live Step Execution Loop (`script_generator_agent.py`)
When `generate_accurate_test_script(target_url, steps)` is called:
1. **Launch Browser (Python Playwright):** Use `async with async_playwright() as p:` and launch `await p.chromium.launch(headless=True)` (allow `HEADLESS_AGENT=false` env override for debugging), then navigate to `target_url`.
2. **Execute Steps Against Live Page:**
   Loop through each `step` in the input `test_case`:
   - Allow up to `6` micro-actions per step (`while not step_completed and sub_attempts < 6:`), since a single step like *"Login"* or *"Enter 5-digit PIN"* requires multiple actions.
   - **Observe:** Call `await extract_page_state(page)` to get the live DOM elements and current URL.
   - **Decide:** Send the current step + live DOM elements + `last_error` (if a previous locator failed) to the LLM using Structured JSON output (`AgentDecision` Pydantic schema):
     - `action`: `'click' | 'fill' | 'select_option' | 'press' | 'assert_visible' | 'assert_text' | 'done'`
     - `selector`: Verified Playwright selector string
     - `value`: Optional string to fill, select, press, or assert
     - `ts_playwright_code`: Equivalent **TypeScript** `@playwright/test` statement (e.g., `await page.locator('[data-test="username"]').fill('standard_user');`)
     - `reasoning`: Brief explanation
   - **Act & Self-Heal:**
     - If `decision.action == "done"`, break out of the `while` loop and move to the next step.
     - Otherwise, execute the Playwright action in Python (`click`, `fill`, `select_option`, `press`, `wait_for`) on the live `page` with a `5000ms` timeout.
     - **If it succeeds:** Append the verified action, selector, and `ts_playwright_code` to `recorded_history`.
     - **If it raises an exception:** Catch the error, set `last_error = str(e)`, and let the loop retry so the LLM selects a working locator from the DOM.

### Part 3: Final TypeScript Script Synthesis
Once all steps have been executed and verified in the Python browser:
1. Ensure the browser closes cleanly in a `finally` block.
2. Pass the `target_url`, input `steps`, and verified `recorded_history` trace to the LLM to synthesize a complete, idiomatic **TypeScript `@playwright/test` file (`.spec.ts`)** using **only** the verified selectors from `recorded_history`.
3. Return the TypeScript code string to the caller/API response.

---

## 3. Reference Python Implementation to Integrate

### `dom_distiller.py`
```python
from typing import Any, Dict, List
from playwright.async_api import Page

DOM_EXTRACTION_JS = """
() => {
  const query = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="tab"], [data-testid], [data-test], h1, h2, h3';
  const nodes = Array.from(document.querySelectorAll(query));

  return nodes
    .filter((el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return (
        rect.width > 0 &&
        rect.height > 0 &&
        style.visibility !== 'hidden' &&
        style.display !== 'none'
      );
    })
    .map((el, index) => {
      const tag = el.tagName.toLowerCase();
      const testId = el.getAttribute('data-testid');
      const dataTest = el.getAttribute('data-test');
      const id = el.id;
      const name = el.getAttribute('name') || undefined;
      const placeholder = el.getAttribute('placeholder') || undefined;
      const ariaLabel = el.getAttribute('aria-label') || undefined;
      const type = el.getAttribute('type') || undefined;
      const role = el.getAttribute('role') || undefined;
      const text = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80);

      let recommendedSelector = tag;
      if (testId) recommendedSelector = `[data-testid="${testId}"]`;
      else if (dataTest) recommendedSelector = `[data-test="${dataTest}"]`;
      else if (id && !/^[0-9]|[:.]/.test(id)) recommendedSelector = `#${id}`;
      else if (name) recommendedSelector = `${tag}[name="${name}"]`;
      else if (placeholder) recommendedSelector = `${tag}[placeholder="${placeholder}"]`;
      else if (ariaLabel) recommendedSelector = `${tag}[aria-label="${ariaLabel}"]`;
      else if (text && (tag === 'button' || tag === 'a')) {
        const cleanText = text.slice(0, 40).replace(/"/g, '\\"');
        recommendedSelector = `${tag}:has-text("${cleanText}")`;
      }

      return {
        index,
        tag,
        type,
        role,
        text,
        placeholder,
        name,
        ariaLabel,
        recommendedSelector,
      };
    });
}
"""


async def extract_page_state(page: Page) -> Dict[str, Any]:
    """Extracts ARIA snapshot and visible interactive DOM elements with recommended selectors."""
    aria_snapshot = ""
    try:
        aria_snapshot = await page.locator("body").aria_snapshot()
    except Exception:
        aria_snapshot = "aria_snapshot unavailable"

    interactive_elements: List[Dict[str, Any]] = await page.evaluate(DOM_EXTRACTION_JS)
    return {
        "aria_snapshot": aria_snapshot,
        "interactive_elements": interactive_elements,
    }
```

### `script_generator_agent.py`
```python
import json
import os
import re
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright, Page
from google import genai
from google.genai import types

from .dom_distiller import extract_page_state

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


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
) -> str:
    """
    Executes test steps against target_url in a live Python Playwright browser,
    verifies all selectors, and outputs a TypeScript @playwright/test (.spec.ts) script.
    """
    headless = os.environ.get("HEADLESS_AGENT", "true").lower() != "false"
    recorded_history: List[Dict[str, Any]] = []

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

                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=AgentDecision,
                            temperature=0.1,
                        ),
                    )

                    decision = AgentDecision.model_validate_json(response.text)

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

    final_response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=synthesis_prompt,
        config=types.GenerateContentConfig(temperature=0.1),
    )

    clean_ts_code = re.sub(
        r"^```(?:typescript|ts)?\n?|```$", "", final_response.text.strip(), flags=re.IGNORECASE | re.MULTILINE
    ).strip()

    return clean_ts_code
```

---

## 4. Integration & Acceptance Checklist
1. **Dependencies:** Ensure `playwright` is installed in the Python environment (`pip install playwright` and `playwright install chromium`).
2. **Wire into Existing "Generate Script" Handler:**
   - In the Python route/service that handles **"Generate Script"**, pass the `target_url` and the approved test case steps into `await generate_accurate_test_script(target_url, steps, test_title)`.
   - Return the resulting TypeScript `.spec.ts` string in the existing response format expected by the frontend.
3. **Do Not Touch Other Agents:** Confirm zero changes were made to the Scenario Generation or Test Case Generation agents.