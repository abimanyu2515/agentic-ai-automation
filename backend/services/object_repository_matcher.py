"""
Match a live page element to an object repository key (spec 0004).

The live-verified script generator records raw selectors such as
`[data-test="username"]`. Before it acts, ObjectKeyResolver checks whether a
stored element on a page matching the current URL finds that same DOM node,
the way the run-time `orx` helper would. If one does, the generator writes the
element's key instead of the raw selector, so runs feed element health.

Matching compares DOM nodes, not selector text, because stored locators
(`role: textbox "Username"`) rarely look like recorded selectors.

With a harvest callback, a miss on a path not yet harvested in this session
snapshots the page with the shared extractor, lets the callback ingest it, and
matches again. So the first generation writes keys even on an empty repository.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional
from urllib.parse import urlsplit

from backend.models.object_repository import ObjectLocator, ProjectObjectRepository
from backend.services.object_repository_harvest import extractor_source, normalize_path
from backend.services.object_repository_service import matching_pages

# (path, raw extracted elements) -> the repository after ingesting them, or None.
Harvest = Callable[[str, list], Optional[ProjectObjectRepository]]

logger = logging.getLogger(__name__)

_SAME_NODE_JS = "(element, target) => element === target"


def playwright_locator(page, locator: ObjectLocator):
    """The Python Playwright locator for a stored locator, mapped like orx in object_repository_script."""
    value, options = locator.value, locator.options or {}
    strategy = locator.strategy
    if strategy == "role":
        kwargs = {key: options[key] for key in ("name", "exact") if key in options}
        return page.get_by_role(value, **kwargs)
    if strategy == "label":
        return page.get_by_label(value, exact=options.get("exact"))
    if strategy == "placeholder":
        return page.get_by_placeholder(value)
    if strategy == "text":
        return page.get_by_text(value, exact=options.get("exact"))
    if strategy == "test_id":
        return page.get_by_test_id(value)
    if strategy == "xpath":
        return page.locator("xpath=" + value)
    return page.locator(value)


class ObjectKeyResolver:
    """Callable `await resolver(page, selector) -> key | None`. Never raises."""

    def __init__(
        self,
        repository: ProjectObjectRepository,
        budget_seconds: float = 2.0,
        harvest: Optional[Harvest] = None,
        harvest_budget_seconds: float = 5.0,
        max_pages: int = 20,
    ):
        self.repository = repository
        self.budget_seconds = budget_seconds
        self.harvest = harvest
        self.harvest_budget_seconds = harvest_budget_seconds
        self.max_pages = max_pages
        self.harvested_paths: set[str] = set()

    async def __call__(self, page, selector: str | None) -> str | None:
        if not selector:
            return None
        key = await self._match(page, selector)
        if key is None and await self._harvest_page(page):
            key = await self._match(page, selector)
        return key

    async def _match(self, page, selector: str) -> str | None:
        try:
            return await asyncio.wait_for(self._resolve(page, selector), self.budget_seconds)
        except Exception as exc:
            logger.info(
                "Object repository key lookup skipped: selector=%s error=%s", selector, type(exc).__name__
            )
            return None

    async def _harvest_page(self, page) -> bool:
        """Snapshot and ingest the current path once per session. True when the repository changed."""
        if self.harvest is None or len(self.harvested_paths) >= self.max_pages:
            return False
        path = normalize_path(page.url)
        if path in self.harvested_paths:
            return False
        self.harvested_paths.add(path)
        try:
            result = await asyncio.wait_for(page.evaluate(extractor_source(), {}), self.harvest_budget_seconds)
            elements = (result or {}).get("elements") or []
            if not elements:
                return False
            # Synchronous on purpose: the store's session stays on this thread.
            repository = self.harvest(path, elements)
        except Exception as exc:
            logger.info("Object repository harvest during generation skipped: path=%s error=%s", path, type(exc).__name__)
            return False
        if repository is None:
            return False
        self.repository = repository
        return True

    async def _resolve(self, page, selector: str) -> str | None:
        pages = matching_pages(self.repository.active_pages(), urlsplit(page.url).path or "/")
        if not pages:
            return None
        target = await page.locator(selector).first.element_handle(timeout=1000)
        if target is None:
            return None
        try:
            for object_page in pages:
                for element in object_page.active_elements():
                    if await self._finds(page, element.locators, target):
                        return f"{object_page.name}.{element.name}"
            return None
        finally:
            await target.dispose()

    @staticmethod
    async def _finds(page, locators: list[ObjectLocator], target) -> bool:
        """Whether orx would pick target: its first visible locator match is the target node."""
        for locator in sorted(locators, key=lambda item: item.position):
            try:
                candidate = playwright_locator(page, locator).first
                if not await candidate.count() or not await candidate.is_visible():
                    continue
                return bool(await candidate.evaluate(_SAME_NODE_JS, target))
            except Exception:
                continue
        return False
