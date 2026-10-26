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
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit
from uuid import UUID

from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.models.test_case import TestCase

logger = logging.getLogger(__name__)


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
    ) -> dict[str, Any]:
        """Main reconnaissance logic using Playwright."""
        from playwright.async_api import async_playwright

        # Determine which pages to inspect based on test case steps
        urls_to_visit = self._derive_urls_from_test_cases(target_url, test_cases)
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
                for url in urls_to_visit[:5]:
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
            return {
                "url": self._safe_page_url(url),
                "error": "Navigation failed",
                "forms": [],
                "elements": [],
                "navigation": [],
                "common_elements": [],
                "visible_text": [],
            }

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

        return page_info

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
            field.get("type") == "password"
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
            fields = form.get("fields", [])
            username_field = next(
                (
                    field for field in fields
                    if field.get("type") != "password"
                    and any(
                        token in str(field.get("label", "")).casefold()
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
            text = (await locator.inner_text()).strip()
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
            fields = (await form.locator("input, select, textarea").all())[:50]
            for field in fields:
                field_info = await self._extract_field_info(page, field)
                if field_info:
                    form_info["fields"].append(field_info)

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
                    or await submit_btn.inner_text()
                    or await submit_btn.get_attribute("value")
                    or ""
                )
                form_info["submit_button"] = {
                    "selector": submit_selector,
                    "name": " ".join(submit_text.split())[:100],
                    "match_count": await page.locator(submit_selector).count()
                    if submit_selector else 0,
                }
            forms.append(form_info)

        return forms

    async def _extract_field_info(self, page, field) -> dict | None:
        """Extract information from a form field."""
        if not await field.is_visible():
            return None
        tag_name = await field.evaluate("el => el.tagName.toLowerCase()")
        field_type = (await field.get_attribute("type") or "text").casefold()
        name = await field.get_attribute("name") or ""
        label_text = await field.evaluate(
            """el => Array.from(el.labels || [])
              .map(label => label.innerText.trim())
              .filter(Boolean)
              .join(' ')"""
        )
        label_text = label_text or await field.get_attribute("aria-label") or name or "Unlabeled"
        if tag_name == "select":
            control_type = "select"
        elif field_type in ("checkbox", "radio", "password", "email", "search", "number"):
            control_type = field_type
        else:
            control_type = "text"

        selector = await self._get_best_selector(field)
        return {
            "type": control_type,
            "role": "combobox" if control_type == "select" else (
                "textbox" if control_type not in ("checkbox", "radio") else control_type
            ),
            "label": " ".join(label_text.split())[:100],
            "selector": selector,
            "match_count": await page.locator(selector).count() if selector else 0,
            "placeholder": ((await field.get_attribute("placeholder")) or "")[:100],
            "required": await field.get_attribute("required") is not None,
        }

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
                or await btn.inner_text()
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
            text = (await link.inner_text()).strip()
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
                text = (await link.inner_text()).strip()
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