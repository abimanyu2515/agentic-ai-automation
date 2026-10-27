"""Live DOM distiller for the test script generator agent.

Extracts only actionable state from a live Playwright page: the ARIA
snapshot and the visible interactive elements, each with a recommended
selector. Raw page HTML is never sent to the LLM.
"""

from typing import Any, Dict, List

from playwright.async_api import Page

DOM_EXTRACTION_JS = r"""
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
