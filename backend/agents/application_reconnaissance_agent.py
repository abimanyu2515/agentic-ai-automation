"""
ApplicationReconnaissanceAgent

Purpose:
    Performs live DOM inspection of the target application to extract real
    selectors, element structures, and page context for test script generation.

Responsibilities:
    - Launch Playwright browser and navigate to project target URL
    - Inspect visible controls and relevant text without reading field values
    - Submit an invalid login only when a safe project and explicit case data allow it
    - Return bounded, sanitized page evidence for PlaywrightAgent

Workflow Position:
    HumanApprovalAgent (Gate 2 - test case approval)
    ↓
    ApplicationReconnaissanceAgent (NEW)
    ↓
    PlaywrightAgent

Inputs:
    - Selected project id and target URL
    - Approved cases and explicit test data for invalid login probes

Outputs:
    - Sanitized page evidence for grounded script generation and validation

Security:
    - Project credentials are never loaded during inspection
    - Field values, password values, query strings, and full page source are not returned

Design Decision:
    Runs once per approved batch. A single repair attempt may request fresh evidence.
"""

import asyncio
import ipaddress
import json
import logging
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import UUID

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase

logger = logging.getLogger(__name__)

# Per form cap on fields (and on hidden buttons). Multi-step forms list every
# step's fields, so this is above a single screen's count.
MAX_FORM_FIELDS = 100

# Steps that move by clicking a named link, for example "Click Automobile in
# the top menu" or "Select Automobile from the navigation".
_NAVIGATION_STEP = re.compile(
    r"^\s*(?:\d+[.)]\s*)?(?:click|select|choose|open|go to|navigate to|tap)\b", re.IGNORECASE
)
MAX_FOLLOWED_LINKS = 3

# Multi-step forms (wizards) keep every step on one URL and show one at a time.
# A step group is the outermost set of same tag and class siblings inside the
# form that each hold a button, with exactly one of them visible. Seen live: a
# script ticked a Product Data checkbox after clicking that step's Next, so
# each field and button is tagged with its step, and each step names the button
# that leaves it.
_STEP_BUTTONS = "button, input[type='submit'], input[type='button'], [role='button']"
_STEP_GROUP_JS = """
  const BUTTONS = "button, input[type='submit'], input[type='button'], [role='button']";
  const isVisible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
    && getComputedStyle(el).visibility !== 'hidden';
  const keyOf = el => el.tagName + '|' + (typeof el.className === 'string' ? el.className.trim() : '');
  const stepGroup = form => {
    for (const parent of [form, ...form.querySelectorAll('*')]) {
      const groups = new Map();
      for (const child of parent.children) {
        const key = keyOf(child);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(child);
      }
      for (const members of groups.values()) {
        if (members.length < 2) continue;
        if (!members.every(member => member.querySelector(BUTTONS))) continue;
        if (members.filter(isVisible).length !== 1) continue;
        return members;
      }
    }
    return null;
  };
"""
_STEP_OF_JS = (
    "el => {" + _STEP_GROUP_JS + """
  const form = el.closest('form');
  const group = form && stepGroup(form);
  if (!group) return null;
  const index = group.findIndex(member => member.contains(el));
  return index < 0 ? null : index + 1;
}"""
)
_FORM_STEPS_JS = (
    "form => {" + _STEP_GROUP_JS + """
  const group = stepGroup(form);
  if (!group) return null;
  const textOf = el => ((el.innerText || el.textContent || '').split('\\n')[0] || '')
    .replace(/\\s+/g, ' ').trim().replace(/\\s*\\d+$/, '');
  // Step names: a tab or progress list outside the steps with one item per
  // step, else each step's first heading or legend.
  let names = null;
  for (const list of document.querySelectorAll("[role='tablist'], nav, ul, ol")) {
    if (group.some(member => member.contains(list) || list.contains(member))) continue;
    const items = [...list.children];
    if (items.length !== group.length) continue;
    const texts = items.map(textOf);
    if (texts.every(Boolean)) { names = texts; break; }
  }
  const all = [...form.querySelectorAll(BUTTONS)];
  const buttonText = button => (button.getAttribute('aria-label') || button.innerText
    || button.value || '').replace(/\\s+/g, ' ').trim();
  return group.map((member, index) => {
    const heading = member.querySelector("h1, h2, h3, h4, h5, h6, legend, [role='heading']");
    const buttons = [...member.querySelectorAll(BUTTONS)];
    const prev = buttons.find(button => /prev|back|«/i.test(buttonText(button)));
    // The last step's button submits; it does not lead to another step.
    const next = index === group.length - 1 ? null
      : buttons.find(button => /next|continue|proceed|»/i.test(buttonText(button)))
        || buttons.filter(button => button !== prev).pop();
    return {
      step: index + 1,
      name: ((names && names[index]) || (heading && textOf(heading)) || `Step ${index + 1}`).slice(0, 100),
      visible: isVisible(member),
      next_index: next ? all.indexOf(next) : -1,
      prev_index: prev ? all.indexOf(prev) : -1,
    };
  });
}"""
)


class ApplicationInspectionError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class ApplicationReconnaissanceAgent(BaseAgent):
    """Live DOM inspection agent for application reconnaissance."""

    name = "Application Reconnaissance Agent"

    def __init__(self, repo=None):
        self.repo = repo

    def run(self, state: WorkflowState) -> WorkflowState:
        if state.requirement is None:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        approved = state.approved_test_cases()
        if not approved:
            state.add_log(f"{self.name}: no approved test cases to inspect")
            return state

        state.add_log(f"{self.name} started")

        # Resolve project configuration
        target_url, framework = self._resolve_project_config(state)

        if not target_url:
            raise ApplicationInspectionError(
                "target_unreachable",
                f"{self.name}: selected project has no configured target URL",
            )

        # Run async reconnaissance
        dom_context = asyncio.run(
            self._reconnoiter(
                state=state,
                target_url=target_url,
                test_cases=approved,
                allow_invalid_login_probe=True,
            )
        )

        # Object repository candidates feed ingest_recon only, never the prompt.
        self.pop_object_evidence(dom_context)
        pages = dom_context.get("pages", [])
        successful_pages = [page for page in pages if not page.get("error")]
        if not successful_pages:
            state.dom_context = {"pages": [], "global": {"navigation": [], "common_elements": []}}
            raise ApplicationInspectionError(
                "target_unreachable",
                f"{self.name}: the selected project's target could not be inspected",
            )
        dom_context["pages"] = successful_pages
        state.dom_context = dom_context
        state.add_log(f"{self.name} finished: inspected {len(successful_pages)} page(s)")
        return state

    def inspect_project(
        self,
        project_id: UUID,
        test_cases: list[TestCase],
        allow_invalid_login_probe: bool,
    ) -> dict[str, Any]:
        project = self.repo.get_project(project_id) if self.repo else None
        if not project:
            raise ApplicationInspectionError(
                "target_unreachable",
                f"{self.name}: selected project was not found",
            )
        if not project.is_safe_test_environment:
            raise ApplicationInspectionError(
                "unsafe_test_environment",
                f"{self.name}: the selected project is not marked as a safe test environment",
            )
        if not project.target_url:
            raise ApplicationInspectionError(
                "target_unreachable",
                f"{self.name}: selected project has no configured target URL",
            )
        logger.info("%s started: project_id=%s", self.name, project_id)
        evidence = asyncio.run(
            self._reconnoiter(
                state=None,
                target_url=project.target_url,
                test_cases=test_cases,
                allow_invalid_login_probe=allow_invalid_login_probe,
            )
        )
        logger.info(
            "%s finished: project_id=%s page_count=%s",
            self.name,
            project_id,
            len(evidence.get("pages", [])),
        )
        return evidence

    def inspect_urls(self, project_id: UUID, urls: list[str]) -> dict[str, Any]:
        """Inspect explicit same origin URLs for an object repository scan."""
        project = self.repo.get_project(project_id) if self.repo else None
        if not project or not project.target_url:
            raise ApplicationInspectionError(
                "target_unreachable",
                f"{self.name}: selected project has no configured target URL",
            )
        if not project.is_safe_test_environment:
            raise ApplicationInspectionError(
                "unsafe_test_environment",
                f"{self.name}: the selected project is not marked as a safe test environment",
            )
        origin = self._safe_origin(project.target_url)
        same_origin = [url for url in urls if self._safe_origin(url) == origin]
        logger.info("%s scan started: project_id=%s url_count=%s", self.name, project_id, len(same_origin))
        return asyncio.run(
            self._reconnoiter(
                state=None,
                target_url=project.target_url,
                test_cases=[],
                urls=same_origin or [project.target_url],
            )
        )

    @staticmethod
    def pop_object_evidence(evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Remove and return the object repository candidates from evidence.

        Each item is {"url": page URL, "elements": [...]} for ingest_recon.
        The remaining evidence is what the prompt receives, unchanged.
        """
        if not evidence:
            return []
        return evidence.pop("_object_repository", None) or []

    def _resolve_project_config(self, state: WorkflowState) -> tuple[str | None, str]:
        """Resolve only the explicitly selected project configuration."""
        if not self.repo or not state.requirement:
            raise ValueError(f"{self.name} requires a selected project and repository")

        if state.project_id is not None:
            target_project = self.repo.get_project(state.project_id)
            if target_project is None or state.requirement.id not in target_project.requirements:
                raise ValueError(f"{self.name}: requirement does not belong to the selected project")
        else:
            matching_projects = [
                project for project in self.repo.list_projects()
                if state.requirement.id in project.requirements
            ]
            if len(matching_projects) != 1:
                raise ValueError(f"{self.name}: requirement must resolve to exactly one project")
            target_project = matching_projects[0]

        if not target_project.is_safe_test_environment:
            raise ApplicationInspectionError(
                "unsafe_test_environment",
                f"{self.name}: the selected project is not marked as a safe test environment",
            )

        return (
            target_project.target_url,
            target_project.framework or "playwright",
        )

    async def _reconnoiter(
        self,
        state: WorkflowState | None,
        target_url: str,
        test_cases: list[TestCase],
        allow_invalid_login_probe: bool = False,
        urls: list[str] | None = None,
    ) -> dict[str, Any]:
        """Main reconnaissance logic using Playwright."""
        from playwright.async_api import async_playwright

        # Determine which pages to inspect based on test case steps, or the
        # explicit list a scan passes.
        urls_to_visit = list(dict.fromkeys(urls)) if urls else self._derive_urls_from_test_cases(target_url, test_cases)
        visit_limit = 20 if urls else 5
        target_origin = self._safe_origin(target_url)
        if any(self._safe_origin(url) != target_origin for url in urls_to_visit):
            raise ApplicationInspectionError(
                "insufficient_evidence",
                "Test steps include a destination outside the selected project origin",
            )

        dom_context = {"pages": [], "global": {"navigation": [], "common_elements": []}}

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 720})

                async def restrict_requests(route) -> None:
                    destination = route.request.url
                    if (
                        self._safe_origin(destination) != target_origin
                        or self._resolves_to_link_local(destination)
                    ):
                        await route.abort()
                        return
                    await route.continue_()

                await context.route("**/*", restrict_requests)
                page = await context.new_page()
                for url in urls_to_visit[:visit_limit]:
                    if state:
                        state.add_log(f"[{self.name}] Inspecting origin: {self._safe_origin(url)}")
                    page_info = await self._inspect_page(
                        page=page,
                        url=url,
                        test_cases=test_cases,
                        allow_invalid_login_probe=allow_invalid_login_probe,
                    )
                    if page_info:
                        dom_context["pages"].append(page_info)
                if not urls and test_cases:
                    for page_info in await self._follow_step_links(page, target_url, test_cases):
                        # A page reached the way the steps reach it replaces a
                        # direct load of the same URL: apps such as a quote form
                        # that reads the chosen product from a cookie set by the
                        # menu show different fields when opened directly.
                        dom_context["pages"] = [
                            existing for existing in dom_context["pages"]
                            if existing.get("url") != page_info.get("url")
                        ]
                        dom_context["pages"].append(page_info)

                dom_context["_object_repository"] = [
                    {"url": page_info["url"], "elements": page_info.pop("_objects", [])}
                    for page_info in dom_context["pages"]
                    if not page_info.get("error")
                ]
                if dom_context["pages"]:
                    first_page = dom_context["pages"][0]
                    dom_context["global"]["navigation"] = first_page.get("navigation", [])
                    dom_context["global"]["common_elements"] = first_page.get("common_elements", [])
            finally:
                await browser.close()

        return dom_context

    @staticmethod
    def _safe_origin(url: str) -> str:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if not parsed.scheme or not hostname:
            return "unknown"
        host = hostname.lower()
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            port = parsed.port
        except ValueError:
            return "unknown"
        if (parsed.scheme.lower() == "http" and port == 80) or (
            parsed.scheme.lower() == "https" and port == 443
        ):
            port = None
        netloc = f"{host}:{port}" if port else host
        return f"{parsed.scheme.lower()}://{netloc}"

    @classmethod
    def _safe_page_url(cls, url: str) -> str:
        parsed = urlsplit(url)
        origin = cls._safe_origin(url)
        return f"{origin}{parsed.path[:300]}" if origin != "unknown" else "unknown"

    @staticmethod
    def _resolves_to_link_local(url: str) -> bool:
        hostname = urlsplit(url).hostname
        if not hostname:
            return True
        try:
            addresses = {
                result[4][0]
                for result in socket.getaddrinfo(hostname, None)
            }
        except OSError:
            return False
        return any(
            ipaddress.ip_address(address).is_link_local
            for address in addresses
        )

    def _derive_urls_from_test_cases(self, base_url: str, test_cases: list[TestCase]) -> list[str]:
        """Derive URLs to inspect from test case steps and preconditions."""
        urls = [base_url]

        for tc in test_cases:
            # Check preconditions for URLs
            for pc in tc.preconditions:
                extracted = self._extract_urls_from_text(pc, base_url)
                urls.extend(extracted)

            # Check steps for navigation hints
            for step in tc.steps:
                extracted = self._extract_urls_from_text(step, base_url)
                urls.extend(extracted)

        return list(dict.fromkeys(urls))

    def _extract_urls_from_text(self, text: str, base_url: str) -> set[str]:
        """Extract potential URLs from text."""
        import re
        urls = set()

        # Full URLs
        url_pattern = r'https?://[^\s\'"<>]+'
        for match in re.finditer(url_pattern, text):
            urls.add(match.group().rstrip(").,;"))

        # Relative paths
        path_pattern = r'(?:go to|navigate to|visit|open)\s+([/\w\-_.]+)'
        for match in re.finditer(path_pattern, text, re.IGNORECASE):
            path = match.group(1)
            if path.startswith('/'):
                from urllib.parse import urljoin
                urls.add(urljoin(base_url, path))

        return urls

    async def _inspect_page(
        self,
        page,
        url: str,
        test_cases: list[TestCase],
        allow_invalid_login_probe: bool,
    ) -> dict[str, Any] | None:
        """Inspect a single page and extract DOM context."""
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.locator("body").wait_for(state="visible", timeout=5000)
        except Exception:
            return self._error_page(url, "Navigation failed")
        return await self._inspect_loaded_page(page, url, test_cases, allow_invalid_login_probe)

    async def _follow_step_links(
        self,
        page,
        target_url: str,
        test_cases: list[TestCase],
    ) -> list[dict[str, Any]]:
        """Inspect the pages reached by clicking links that the steps name.

        A step such as "Click Automobile in the top menu" names a link on the
        page the flow is on. Starting from the target URL, each such step
        clicks the visible link with that exact name and inspects the page it
        leads to, so the evidence matches what the script will see. Only
        links are followed (plain navigation, nothing is submitted), and the
        browser's request filter still blocks other origins.
        """
        steps = [
            step for test_case in test_cases
            for step in [*test_case.preconditions, *test_case.steps]
            if _NAVIGATION_STEP.search(step)
        ]
        if not steps:
            return []
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            return []
        pages = []
        for step in steps:
            if len(pages) >= MAX_FOLLOWED_LINKS:
                break
            try:
                names = await page.locator("a[href]").evaluate_all(
                    """links => links
                      .filter(a => a.offsetWidth || a.offsetHeight || a.getClientRects().length)
                      .map(a => (a.innerText || a.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim())
                      .filter(name => name.length >= 3 && name.length <= 60)"""
                )
            except Exception:
                continue
            step_text = f" {' '.join(step.casefold().split())} "
            matches = sorted(
                {name for name in names if re.search(rf"(?<!\w){re.escape(name.casefold())}(?!\w)", step_text)},
                key=len,
                reverse=True,
            )
            if not matches:
                continue
            link = page.get_by_role("link", name=matches[0], exact=True).first
            try:
                await link.click(timeout=5000)
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
                await page.locator("body").wait_for(state="visible", timeout=5000)
            except Exception:
                continue
            page_info = await self._inspect_loaded_page(page, page.url, test_cases, False)
            if page_info and not page_info.get("error"):
                page_info["reached_by"] = f"link {matches[0]!r}"
                pages.append(page_info)
        return pages

    async def _inspect_loaded_page(
        self,
        page,
        url: str,
        test_cases: list[TestCase],
        allow_invalid_login_probe: bool,
    ) -> dict[str, Any] | None:
        # One page that can't be read must not fail the whole inspection or
        # scan; it is reported as an error page, which callers already skip.
        try:
            forms = await self._extract_forms(page)
            if allow_invalid_login_probe:
                for test_case in test_cases:
                    credentials = self._explicit_invalid_login_data(test_case)
                    if credentials and await self._looks_like_login_page(forms):
                        await self._submit_invalid_login(page, forms, credentials)
                        break

            page_info = {
                "url": self._safe_page_url(page.url),
                "title": self._sanitize_text((await page.title())[:200], test_cases),
                "forms": forms,
                "elements": await self._extract_interactive_elements(page),
                "navigation": await self._extract_navigation(page),
                "common_elements": await self._extract_common_elements(page),
                "visible_text": await self._extract_visible_text(page, test_cases),
            }
        except Exception as exc:
            logger.warning("Page inspection failed: url=%s error=%s", self._safe_page_url(url), type(exc).__name__)
            return self._error_page(url, "Inspection failed")
        try:
            page_info["_objects"] = await self._extract_object_elements(page, test_cases)
        except Exception as exc:
            logger.warning("Object repository extraction failed: error=%s", type(exc).__name__)
            page_info["_objects"] = []

        return page_info

    @classmethod
    def _error_page(cls, url: str, error: str) -> dict[str, Any]:
        return {
            "url": cls._safe_page_url(url),
            "error": error,
            "forms": [],
            "elements": [],
            "navigation": [],
            "common_elements": [],
            "visible_text": [],
        }

    @staticmethod
    async def _html_text(locator) -> str:
        """The element's innerText, or "" for non HTML nodes such as SVG icons.

        Text selectors like [class*='error'] and a[href] also match SVG
        elements, and Playwright's inner_text() raises on those.
        """
        from playwright.async_api import Error as PlaywrightError

        try:
            return await locator.inner_text()
        except PlaywrightError as exc:
            if "not an HTMLElement" not in str(exc):
                raise
            return ""

    @staticmethod
    def _explicit_invalid_login_data(test_case: TestCase) -> tuple[str, str] | None:
        case_text = " ".join(
            [test_case.title, test_case.expected_result, *test_case.steps]
        ).casefold()
        if not any(word in case_text for word in ("invalid", "incorrect", "wrong")):
            return None
        data = test_case.test_data
        if test_case.test_data_sets:
            data = test_case.test_data_sets[0]
        username = next(
            (
                value for key, value in data.items()
                if key.casefold() in {"username", "user", "user_name", "email", "login"}
            ),
            None,
        )
        password = next(
            (
                value for key, value in data.items()
                if key.casefold() in {"password", "pass", "passwd"}
            ),
            None,
        )
        if not username or not password:
            return None
        return str(username), str(password)

    @staticmethod
    def _looks_like_login_page(forms: list[dict[str, Any]]) -> bool:
        return any(
            field.get("type") == "password" and field.get("visible", True)
            for form in forms
            for field in form.get("fields", [])
        )

    async def _submit_invalid_login(
        self,
        page,
        forms: list[dict[str, Any]],
        credentials: tuple[str, str],
    ) -> None:
        username, password = credentials
        for form in forms:
            fields = [field for field in form.get("fields", []) if field.get("visible", True)]
            username_field = next(
                (
                    field for field in fields
                    if field.get("type") != "password"
                    and any(
                        token in f"{field.get('label', '')} {field.get('name', '')}".casefold()
                        for token in ("user", "email", "login")
                    )
                    and field.get("match_count") == 1
                ),
                None,
            )
            password_field = next(
                (
                    field for field in fields
                    if field.get("type") == "password"
                    and field.get("match_count") == 1
                ),
                None,
            )
            submit = form.get("submit_button") or {}
            if not username_field or not password_field or submit.get("match_count") != 1:
                continue
            await page.locator(username_field["selector"]).fill(username)
            await page.locator(password_field["selector"]).fill(password)
            await page.locator(submit["selector"]).click()
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError

            try:
                await page.locator(
                    "[role='alert']:visible, [aria-live]:visible, [class*='error' i]:visible"
                ).first.wait_for(state="visible", timeout=3000)
            except PlaywrightTimeoutError:
                pass
            return

    async def _extract_visible_text(
        self,
        page,
        test_cases: list[TestCase],
    ) -> list[str]:
        texts = []
        for locator in await page.locator(
            "h1, h2, [role='alert'], [aria-live='assertive'], [aria-live='polite'], [class*='error' i]"
        ).all():
            if not await locator.is_visible():
                continue
            text = (await self._html_text(locator)).strip()
            if text:
                texts.append(self._sanitize_text(text, test_cases))
            if len(texts) == 12:
                break
        return list(dict.fromkeys(texts))

    @staticmethod
    def _sanitize_text(text: str, test_cases: list[TestCase]) -> str:
        sanitized = " ".join(text.split())[:300]
        for test_case in test_cases:
            data_sets = [test_case.test_data, *test_case.test_data_sets]
            for data_set in data_sets:
                for value in data_set.values():
                    if value:
                        sanitized = sanitized.replace(str(value), "[redacted]")
        return sanitized

    async def _extract_forms(self, page) -> list[dict]:
        """Extract all forms and their fields."""
        forms = []
        form_elements = (await page.locator("form").all())[:20]

        for form in form_elements:
            if not await form.is_visible():
                continue
            selector = await self._get_best_selector(form)
            form_info = {
                "selector": selector,
                "fields": [],
                "submit_button": None,
                "match_count": await page.locator(selector).count() if selector else 0,
            }
            # Hidden fields are kept (visible: false): a multi-step form such as
            # a wizard on one URL shows its later steps only after actions, so
            # without them the model has no observed selector for those steps.
            steps = await self._form_steps(page, form)
            if steps:
                form_info["steps"] = steps
            fields = (await form.locator(
                "input:not([type='hidden']), select, textarea"
            ).all())[:MAX_FORM_FIELDS]
            for field in fields:
                field_info = await self._extract_field_info(page, field)
                if field_info:
                    if steps:
                        await self._tag_step(field, field_info)
                    form_info["fields"].append(field_info)
            hidden_buttons = []
            for button in (await form.locator(
                "button, input[type='submit'], input[type='button']"
            ).all())[:MAX_FORM_FIELDS]:
                if await button.is_visible():
                    continue
                button_selector = await self._get_best_selector(button)
                button_text = (
                    await button.get_attribute("aria-label")
                    or await button.text_content()
                    or await button.get_attribute("value")
                    or ""
                )
                button_info = {
                    "name": " ".join(button_text.split())[:100],
                    "selector": button_selector,
                    "match_count": await page.locator(button_selector).count()
                    if button_selector else 0,
                    "visible": False,
                }
                if steps:
                    await self._tag_step(button, button_info)
                hidden_buttons.append(button_info)
            if hidden_buttons:
                form_info["hidden_buttons"] = hidden_buttons

            submit_candidates = await form.locator(
                'button[type="submit"], input[type="submit"], button'
            ).all()
            submit_btn = None
            for button in submit_candidates:
                if await button.is_visible():
                    submit_btn = button
                    break
            if submit_btn:
                submit_selector = await self._get_best_selector(submit_btn)
                submit_text = (
                    await submit_btn.get_attribute("aria-label")
                    or await self._html_text(submit_btn)
                    or await submit_btn.get_attribute("value")
                    or ""
                )
                form_info["submit_button"] = {
                    "selector": submit_selector,
                    "name": " ".join(submit_text.split())[:100],
                    "match_count": await page.locator(submit_selector).count()
                    if submit_selector else 0,
                }
                if steps:
                    await self._tag_step(submit_btn, form_info["submit_button"])
            forms.append(form_info)

        return forms

    async def _form_steps(self, page, form) -> list[dict[str, Any]] | None:
        """The steps of a multi-step form, in order, or None for a plain form.

        Each step has its 1-based number, a name (from the form's tab or
        progress list, else its heading), whether it was visible at load, and
        the unique selectors of the buttons that move to the next and previous
        step, so a script can tell which controls it may use before each
        "Next" click.
        """
        try:
            raw = await form.evaluate(_FORM_STEPS_JS)
        except Exception:
            return None
        if not raw:
            return None
        buttons = form.locator(_STEP_BUTTONS)
        steps = []
        for item in raw:
            step = {
                "step": int(item["step"]),
                "name": " ".join(str(item.get("name") or "").split())[:100],
                "visible": bool(item.get("visible")),
            }
            for key in ("next", "prev"):
                index = item.get(f"{key}_index", -1)
                if not isinstance(index, int) or index < 0:
                    continue
                selector = await self._get_best_selector(buttons.nth(index))
                if selector and await page.locator(selector).count() == 1:
                    step[f"{key}_button"] = selector
            steps.append(step)
        return steps

    @staticmethod
    async def _tag_step(element, info: dict[str, Any]) -> None:
        """Add the multi-step form step that holds this element, when known."""
        try:
            step = await element.evaluate(_STEP_OF_JS)
        except Exception:
            return
        if isinstance(step, int):
            info["step"] = step

    async def _extract_field_info(self, page, field) -> dict | None:
        """Extract information from a form field, visible or hidden."""
        visible = await field.is_visible()
        tag_name = await field.evaluate("el => el.tagName.toLowerCase()")
        field_type = (await field.get_attribute("type") or "text").casefold()
        name = await field.get_attribute("name") or ""
        label_text = await field.evaluate(
            """el => Array.from(el.labels || [])
              .map(label => label.innerText.trim())
              .filter(Boolean)
              .join(' ')"""
        )
        # Only text Playwright's getByLabel can resolve counts as a label. A
        # name attribute is reported separately, so a page whose <label> is
        # not tied to its field (no `for`, not wrapping it) yields an empty
        # label and the model uses the observed selector instead.
        label_text = label_text or await field.get_attribute("aria-label") or ""
        if tag_name == "select":
            control_type = "select"
        elif field_type in ("checkbox", "radio", "password", "email", "search", "number"):
            control_type = field_type
        else:
            control_type = "text"

        selector = await self._get_best_selector(field)
        info = {
            "type": control_type,
            "role": "combobox" if control_type == "select" else (
                "textbox" if control_type not in ("checkbox", "radio") else control_type
            ),
            "label": " ".join(label_text.split())[:100],
            "name": name[:100],
            "selector": selector,
            "match_count": await page.locator(selector).count() if selector else 0,
            "placeholder": ((await field.get_attribute("placeholder")) or "")[:100],
            "required": await field.get_attribute("required") is not None,
            "visible": visible,
        }
        if control_type in ("checkbox", "radio"):
            # The value attribute of an option is authored markup, not user
            # input; it ties a data value to an option without visible text.
            option_value = await field.get_attribute("value")
            if option_value:
                info["value"] = option_value[:100]
            click_selector = await self._label_click_selector(page, field, selector)
            if click_selector:
                info["click_selector"] = click_selector
        return info

    async def _label_click_selector(self, page, field, field_selector: str) -> str | None:
        """A unique selector for the <label> of a checkbox or radio, or None.

        Many forms style these controls by hiding or covering the native input
        (a custom span or icon on top of it), so a click on the input is
        intercepted while a click on its label toggles it on any site. Built
        from plain HTML only: the label's id, its `for` attribute, the input
        it wraps, or the input's name and value attributes (authored markup,
        not something a user typed). Kept only when it matches one element.
        """
        info = await field.evaluate(
            """el => {
              const label = el.closest('label') || (el.labels && el.labels[0]) || null;
              if (!label) return null;
              return {
                id: label.id || '',
                htmlFor: label.getAttribute('for') || '',
                wraps: label.contains(el),
                tag: el.tagName.toLowerCase(),
                name: el.getAttribute('name') || '',
                value: el.getAttribute('value') || '',
              };
            }"""
        )
        if not info:
            return None
        candidates = []
        if info["id"]:
            candidates.append(f"label#{await field.evaluate('(_, id) => CSS.escape(id)', info['id'])}")
        if info["htmlFor"]:
            candidates.append(f"label[for={json.dumps(info['htmlFor'])}]")
        if info["wraps"]:
            if field_selector:
                candidates.append(f"label:has({field_selector})")
            if info["name"] and info["value"]:
                candidates.append(
                    f"label:has({info['tag']}[name={json.dumps(info['name'])}]"
                    f"[value={json.dumps(info['value'])}])"
                )
        for candidate in candidates:
            try:
                if await page.locator(candidate).count() == 1:
                    return candidate
            except Exception:
                continue
        return None

    async def _extract_interactive_elements(self, page) -> list[dict]:
        """Extract buttons, links, and other interactive elements."""
        elements = []

        # Buttons
        buttons = (await page.locator(
            "button, input[type='button'], input[type='submit'], [role='button']"
        ).all())[:50]
        for btn in buttons:
            if not await btn.is_visible():
                continue
            text = (
                await btn.get_attribute("aria-label")
                or await self._html_text(btn)
                or await btn.get_attribute("value")
                or ""
            )
            role = await btn.get_attribute("role") or "button"
            selector = await self._get_best_selector(btn)
            if selector and text.strip():
                elements.append({
                    "role": role,
                    "name": " ".join(text.split())[:100],
                    "selector": selector,
                    "match_count": await page.locator(selector).count(),
                })

        # Links
        links = (await page.locator("a[href]").all())[:50]
        for link in links:
            if not await link.is_visible():
                continue
            text = (await self._html_text(link)).strip()
            href = await link.get_attribute("href") or ""
            selector = await self._get_best_selector(link)
            if selector and text:
                elements.append({
                    "role": "link",
                    "name": " ".join(text.split())[:100],
                    "selector": selector,
                    "match_count": await page.locator(selector).count(),
                    "href": self._safe_page_url(urljoin(page.url, href)),
                })

        return elements

    async def _extract_navigation(self, page) -> list[dict]:
        """Extract navigation elements (menus, breadcrumbs, tabs)."""
        nav_elements = []

        # Common navigation selectors
        nav_selectors = [
            "nav a[href]",
            "[role='navigation'] a[href]",
            ".navbar a[href]",
            ".menu a[href]",
            ".sidebar a[href]",
            "[data-testid*='nav'] a[href]",
        ]

        for selector in nav_selectors:
            links = await page.locator(selector).all()
            for link in links[:50]:
                if not await link.is_visible():
                    continue
                text = (await self._html_text(link)).strip()
                href = await link.get_attribute("href") or ""
                if text and href:
                    link_selector = await self._get_best_selector(link)
                    nav_elements.append({
                        "name": " ".join(text.split())[:100],
                        "selector": link_selector,
                        "match_count": await page.locator(link_selector).count(),
                        "href": self._safe_page_url(urljoin(page.url, href)),
                    })

        # Deduplicate by href
        seen = set()
        unique = []
        for item in nav_elements:
            if item["href"] not in seen:
                seen.add(item["href"])
                unique.append(item)

        return unique

    async def _extract_common_elements(self, page) -> list[dict]:
        """Extract commonly used elements (headers, footers, modals, etc.)."""
        common = []

        # Headers
        headers = await page.locator("header, [role='banner'], .header").all()
        for h in headers:
            try:
                selector = await self._get_best_selector(h)
                if selector:
                    common.append({"type": "header", "selector": selector})
            except Exception:
                continue

        # Footers
        footers = await page.locator("footer, [role='contentinfo'], .footer").all()
        for f in footers:
            try:
                selector = await self._get_best_selector(f)
                if selector:
                    common.append({"type": "footer", "selector": selector})
            except Exception:
                continue

        # Main content area
        mains = await page.locator("main, [role='main'], .main, .content").all()
        for m in mains:
            try:
                selector = await self._get_best_selector(m)
                if selector:
                    common.append({"type": "main", "selector": selector})
            except Exception:
                continue

        return common

    # ------------------------------------------------ object repository

    async def _extract_object_elements(self, page, test_cases: list[TestCase]) -> list[dict]:
        """Buttons, links, form fields, and navigation links for the object repository.

        Runs the extractor test runs share (spec 0004, AC-21) in one page call,
        then applies the same redaction and token rules a harvest does.
        """
        from backend.services.object_repository_harvest import (
            clean_elements,
            extractor_source,
            values_from_test_data,
        )

        result = await page.evaluate(extractor_source(), {})
        secrets = [value for test_case in test_cases for value in values_from_test_data(test_case)]
        return clean_elements(result.get("elements") or [], secrets)

    async def _get_best_selector(self, locator) -> str:
        """Return a stable selector observed on the current page."""
        testid = await locator.get_attribute("data-testid")
        if testid:
            return f"[data-testid={json.dumps(testid)}]"
        id_attr = await locator.get_attribute("id")
        if id_attr:
            return f"#{await locator.evaluate('(el) => CSS.escape(el.id)')}"
        name = await locator.get_attribute("name")
        if name:
            return f"[name={json.dumps(name)}]"
        aria = await locator.get_attribute("aria-label")
        if aria:
            return f"[aria-label={json.dumps(aria)}]"
        placeholder = await locator.get_attribute("placeholder")
        if placeholder:
            return f"[placeholder={json.dumps(placeholder)}]"
        return await locator.evaluate(
            """el => {
              const parts = [];
              while (el && el.nodeType === Node.ELEMENT_NODE && el.tagName !== 'HTML') {
                let part = el.tagName.toLowerCase();
                let index = 1;
                for (let sibling = el.previousElementSibling; sibling; sibling = sibling.previousElementSibling) {
                  if (sibling.tagName === el.tagName) index++;
                }
                part += `:nth-of-type(${index})`;
                parts.unshift(part);
                el = el.parentElement;
              }
              return parts.join(' > ');
            }"""
        )