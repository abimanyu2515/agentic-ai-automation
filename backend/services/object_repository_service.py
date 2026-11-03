"""
ObjectRepositoryService (spec 0004).

Business rules for the object repository: validation, page and element
writes, rename rewrites, script rendering, the generator's element list,
unknown key checks, run feedback, and the reconnaissance merge.

Every write goes through `store.mutate`, so it is one atomic unit in both the
JSON store and PostgreSQL, and the rules here run the same way on both.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import UUID

from backend.models.object_repository import (
    APPLIED_EXECUTION_HISTORY,
    ELEMENT_TYPES,
    HEALTH_STATES,
    LOCATOR_STRATEGIES,
    MAX_LOCATORS,
    ObjectElement,
    ObjectLocator,
    ObjectPage,
    ProjectObjectRepository,
)
from backend.repository.object_repository_store import (
    ObjectRepositoryIntegrityError,
    ObjectRepositoryUnit,
    get_object_repository_store,
)
from backend.services import object_repository_script as scripts

logger = logging.getLogger(__name__)

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_UUID_SEGMENT = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_MARKER = re.compile(r"\[OBJREPO\]\s*(\{.*\})")
_ALLOWED_OPTIONS = {
    "role": {"name": str, "exact": bool},
    "label": {"exact": bool},
    "text": {"exact": bool},
}
_HEALTH_ORDER = {"healthy": 0, "needs_review": 1, "missing": 2}
_OUTCOME_RANK = {"healthy": 0, "backup": 1, "failed": 2}

Audit = Callable[[str, dict], None]


class ObjectRepositoryError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class ObjectRepositoryNotFound(ObjectRepositoryError):
    status_code = 404


class ObjectRepositoryConflict(ObjectRepositoryError):
    status_code = 409


class ObjectRepositoryInvalid(ObjectRepositoryError):
    status_code = 422


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _settings():
    from backend.config.settings import get_settings

    return get_settings().object_repository


# ---------------------------------------------------------------- validation

def validate_name(name: str | None, max_length: int, label: str) -> str:
    if not isinstance(name, str) or not NAME_PATTERN.match(name) or len(name) > max_length:
        raise ObjectRepositoryInvalid(
            f"{label} must be snake_case (lowercase letters, digits, underscores, starting with a letter) "
            f"and at most {max_length} characters"
        )
    return name


def validate_pattern(pattern: str | None) -> str:
    if (
        not isinstance(pattern, str)
        or not pattern.strip()
        or not pattern.startswith("/")
        or "***" in pattern
        or "?" in pattern
        or len(pattern) > 500
    ):
        raise ObjectRepositoryInvalid(
            "url_pattern must be a path glob that starts with '/', without '***' or '?', at most 500 characters"
        )
    return pattern


def validate_locators(locators: Any) -> list[dict]:
    if not isinstance(locators, list) or not 1 <= len(locators) <= MAX_LOCATORS:
        raise ObjectRepositoryInvalid(f"An element needs 1 to {MAX_LOCATORS} locators")
    cleaned = []
    for index, locator in enumerate(locators):
        if hasattr(locator, "model_dump"):
            locator = locator.model_dump()
        if not isinstance(locator, dict):
            raise ObjectRepositoryInvalid(f"Locator {index} must be an object")
        strategy = locator.get("strategy")
        if strategy not in LOCATOR_STRATEGIES:
            raise ObjectRepositoryInvalid(
                f"Locator {index} has an unknown strategy; use one of {', '.join(LOCATOR_STRATEGIES)}"
            )
        value = locator.get("value")
        if not isinstance(value, str) or not value.strip():
            raise ObjectRepositoryInvalid(f"Locator {index} needs a value")
        options = locator.get("options") or {}
        if not isinstance(options, dict):
            raise ObjectRepositoryInvalid(f"Locator {index} options must be an object")
        allowed = _ALLOWED_OPTIONS.get(strategy, {})
        for key, option_value in options.items():
            expected = allowed.get(key)
            if expected is None or not isinstance(option_value, expected):
                raise ObjectRepositoryInvalid(f"Locator {index} has an option '{key}' that {strategy} does not allow")
        cleaned.append({"strategy": strategy, "value": value, "options": dict(options)})
    return cleaned


def validate_element_type(element_type: str | None) -> str:
    if element_type not in ELEMENT_TYPES:
        raise ObjectRepositoryInvalid(f"element_type must be one of {', '.join(ELEMENT_TYPES)}")
    return element_type


# ------------------------------------------------------------- page matching

def _normalize_path(path: str) -> str:
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/")  # "/" becomes "", matched by "^$" and "/**"


def _pattern_regex(pattern: str) -> re.Pattern:
    regex = ""
    for segment in _normalize_path(pattern).split("/")[1:]:
        if segment == "**":
            regex += "(?:/[^/]+)*"
        else:
            regex += "/" + "[^/]+".join(re.escape(part) for part in segment.split("*"))
    return re.compile(f"^{regex}$")


def pattern_matches(pattern: str, path: str) -> bool:
    return bool(_pattern_regex(pattern).match(_normalize_path(path)))


def matching_pages(pages: list[ObjectPage], path: str) -> list[ObjectPage]:
    """Pages whose pattern matches the path, best match first.

    The pattern with the most literal characters wins; ties go to the longer
    pattern, then the oldest page.
    """
    matches = [page for page in pages if not page.is_deleted and pattern_matches(page.url_pattern, path)]
    return sorted(
        matches,
        key=lambda page: (
            -len(page.url_pattern.replace("*", "")),
            -len(page.url_pattern),
            page.created_at,
        ),
    )


def page_pattern_for_path(path: str) -> str:
    segments = [segment for segment in (path or "/").split("/") if segment]
    pattern = "/" + "/".join(
        "*" if segment.isdigit() or _UUID_SEGMENT.match(segment) else segment
        for segment in segments
    )
    return pattern


def slugify(text: str, max_length: int = 100) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return slug[:max_length].rstrip("_")


def _unique_name(base: str, taken: set[str], max_length: int) -> str:
    base = base[:max_length]
    if base not in taken:
        return base
    counter = 2
    while True:
        suffix = f"_{counter}"
        candidate = base[: max_length - len(suffix)] + suffix
        if candidate not in taken:
            return candidate
        counter += 1


def _locator_summary(element: ObjectElement | None) -> list[str]:
    if element is None:
        return []
    return [f"{locator.strategy}={locator.value}" for locator in element.locators]


def _key_reference(key: str) -> re.Pattern:
    return re.compile(r"""(['"`])""" + re.escape(key) + r"\1")


def _replace_locators(element: ObjectElement, new_locators: list[dict]) -> bool:
    """Replace the locator list, keeping counters of unchanged locators.

    Returns True when the list actually changed.
    """
    old = list(element.locators)
    unused = list(old)
    replacement = []
    now = _now()
    for position, data in enumerate(new_locators):
        candidate = ObjectLocator(position=position, **data)
        match = next((loc for loc in unused if loc.signature() == candidate.signature()), None)
        if match is not None:
            unused.remove(match)
            if match.position != position:
                match = match.model_copy(update={"position": position, "updated_at": now})
            replacement.append(match)
        else:
            replacement.append(candidate)
    changed = [loc.model_dump() for loc in replacement] != [loc.model_dump() for loc in old]
    element.locators = replacement
    return changed


class ObjectRepositoryService:
    def __init__(self, project_repo=None, store=None):
        self._project_repo = project_repo
        self._store = store

    @property
    def project_repo(self):
        if self._project_repo is not None:
            return self._project_repo
        from backend.repository.project_repository import get_project_repository

        return get_project_repository()

    @property
    def store(self):
        return self._store or get_object_repository_store(self.project_repo)

    # ----------------------------------------------------------- helpers

    def _require_project(self, project_id: UUID):
        project = self.project_repo.get_project(project_id)
        if not project:
            raise ObjectRepositoryNotFound("Project not found")
        return project

    def _require_store(self):
        store = self.store
        if store is None:
            raise ObjectRepositoryError("The object repository is not available for this repository provider")
        return store

    def _mutate(self, project_id: UUID, change: Callable[[ObjectRepositoryUnit], Any]):
        try:
            return self._require_store().mutate(project_id, change)
        except ObjectRepositoryIntegrityError as exc:
            raise ObjectRepositoryConflict("A page or element with this name or pattern already exists") from exc

    def load(self, project_id: UUID) -> ProjectObjectRepository:
        store = self.store
        if store is None:
            return ProjectObjectRepository(project_id=project_id)
        return store.load(project_id)

    @staticmethod
    def _active_page(repository: ProjectObjectRepository, page_id: UUID) -> ObjectPage:
        page = repository.find_page(page_id)
        if page is None or page.is_deleted:
            raise ObjectRepositoryNotFound("Page not found")
        return page

    @staticmethod
    def _active_element(repository: ProjectObjectRepository, element_id: UUID) -> tuple[ObjectPage, ObjectElement]:
        found = repository.find_element(element_id)
        if found is None or found[1].is_deleted or found[0].is_deleted:
            raise ObjectRepositoryNotFound("Element not found")
        return found

    @staticmethod
    def _check_page_unique(repository: ProjectObjectRepository, name: str, pattern: str, exclude: UUID | None = None):
        for page in repository.active_pages():
            if page.id == exclude:
                continue
            if page.name == name:
                raise ObjectRepositoryConflict(f"A page named '{name}' already exists")
            if page.url_pattern == pattern:
                raise ObjectRepositoryConflict(f"A page with the pattern '{pattern}' already exists")

    @staticmethod
    def _check_element_unique(page: ObjectPage, name: str, exclude: UUID | None = None):
        if any(e.name == name and e.id != exclude for e in page.active_elements()):
            raise ObjectRepositoryConflict(f"An element named '{name}' already exists on page '{page.name}'")

    @staticmethod
    def _rewrite_keys(unit: ObjectRepositoryUnit, renames: dict[str, str]) -> int:
        """Rewrite exact quoted references to renamed keys in every stored script."""
        if not renames:
            return 0
        rewritten = 0
        for test_case_id, script in unit.test_case_scripts().items():
            if not script:
                continue
            updated = script
            for old_key, new_key in renames.items():
                updated = _key_reference(old_key).sub(lambda m: f"{m.group(1)}{new_key}{m.group(1)}", updated)
            if updated != script:
                unit.set_test_case_script(test_case_id, updated)
                rewritten += 1
        return rewritten

    @staticmethod
    def _count_references(unit: ObjectRepositoryUnit, keys: list[str]) -> int:
        patterns = [_key_reference(key) for key in keys]
        return sum(
            1 for script in unit.test_case_scripts().values()
            if script and any(pattern.search(script) for pattern in patterns)
        )

    # ------------------------------------------------------------- pages

    def list_pages(self, project_id: UUID, limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
        self._require_project(project_id)
        pages = sorted(self.load(project_id).active_pages(), key=lambda page: page.name)
        result = []
        for page in pages[offset: offset + limit]:
            counts = {health: 0 for health in HEALTH_STATES}
            for element in page.active_elements():
                counts[element.health] = counts.get(element.health, 0) + 1
            result.append({"page": page, "counts": counts})
        return result, len(pages)

    def create_page(self, project_id: UUID, name: str, url_pattern: str, audit: Audit | None = None) -> ObjectPage:
        self._require_project(project_id)
        validate_name(name, 200, "Page name")
        validate_pattern(url_pattern)

        def change(unit: ObjectRepositoryUnit) -> ObjectPage:
            self._check_page_unique(unit.repository, name, url_pattern)
            page = ObjectPage(project_id=project_id, name=name, url_pattern=url_pattern)
            unit.repository.pages.append(page)
            return page

        page = self._mutate(project_id, change)
        if audit:
            audit("CREATE", {"entity": "object_page", "id": str(page.id), "name": name, "after": {"url_pattern": url_pattern}})
        return page

    def update_page(self, project_id: UUID, page_id: UUID, changes: dict, audit: Audit | None = None) -> tuple[ObjectPage, int]:
        self._require_project(project_id)
        if "name" in changes:
            validate_name(changes["name"], 200, "Page name")
        if "url_pattern" in changes:
            validate_pattern(changes["url_pattern"])
        captured: dict = {}

        def change(unit: ObjectRepositoryUnit):
            page = self._active_page(unit.repository, page_id)
            captured["before"] = {"name": page.name, "url_pattern": page.url_pattern}
            new_name = changes.get("name", page.name)
            new_pattern = changes.get("url_pattern", page.url_pattern)
            self._check_page_unique(unit.repository, new_name, new_pattern, exclude=page.id)
            renames = {}
            if new_name != page.name:
                renames = {
                    f"{page.name}.{element.name}": f"{new_name}.{element.name}"
                    for element in page.active_elements()
                }
            page.name = new_name
            page.url_pattern = new_pattern
            page.updated_at = _now()
            return page, self._rewrite_keys(unit, renames)

        page, rewritten = self._mutate(project_id, change)
        if audit:
            audit("UPDATE", {
                "entity": "object_page", "id": str(page.id), "name": page.name,
                "before": captured["before"], "after": {"name": page.name, "url_pattern": page.url_pattern},
                "scripts_rewritten": rewritten,
            })
        return page, rewritten

    def delete_page(self, project_id: UUID, page_id: UUID, deleted_by: UUID | None = None, audit: Audit | None = None) -> tuple[int, int]:
        self._require_project(project_id)

        def change(unit: ObjectRepositoryUnit):
            page = self._active_page(unit.repository, page_id)
            now = _now()
            elements = page.active_elements()
            references = self._count_references(unit, [f"{page.name}.{e.name}" for e in elements])
            for element in elements:
                element.is_deleted, element.deleted_at, element.deleted_by = True, now, deleted_by
            page.is_deleted, page.deleted_at, page.deleted_by = True, now, deleted_by
            return page, len(elements), references

        page, deleted, references = self._mutate(project_id, change)
        if audit:
            audit("DELETE", {
                "entity": "object_page", "id": str(page.id), "name": page.name,
                "before": {"url_pattern": page.url_pattern, "elements": deleted}, "after": None,
            })
        return deleted, references

    # ---------------------------------------------------------- elements

    def list_elements(self, project_id: UUID, page_id: UUID, health: str | None = None, limit: int = 100, offset: int = 0) -> tuple[ObjectPage, list[ObjectElement], int]:
        self._require_project(project_id)
        if health is not None and health not in HEALTH_STATES:
            raise ObjectRepositoryInvalid(f"health must be one of {', '.join(HEALTH_STATES)}")
        page = self._active_page(self.load(project_id), page_id)
        elements = sorted(page.active_elements(), key=lambda element: element.name)
        if health:
            elements = [element for element in elements if element.health == health]
        return page, elements[offset: offset + limit], len(elements)

    def create_element(
        self,
        project_id: UUID,
        page_id: UUID,
        name: str,
        element_type: str,
        locators: list,
        description: str | None = None,
        audit: Audit | None = None,
    ) -> tuple[ObjectPage, ObjectElement]:
        self._require_project(project_id)
        validate_name(name, 100, "Element name")
        validate_element_type(element_type)
        cleaned = validate_locators(locators)

        def change(unit: ObjectRepositoryUnit) -> tuple[ObjectPage, ObjectElement]:
            page = self._active_page(unit.repository, page_id)
            self._check_element_unique(page, name)
            element = ObjectElement(
                page_id=page.id,
                name=name,
                element_type=element_type,
                description=description,
                manually_edited=True,
                health="healthy",
                locators=[ObjectLocator(position=i, **data) for i, data in enumerate(cleaned)],
            )
            page.elements.append(element)
            return page, element

        page, element = self._mutate(project_id, change)
        if audit:
            audit("CREATE", {
                "entity": "object_element", "id": str(element.id), "key": f"{page.name}.{element.name}",
                "before": None, "after": {"locators": _locator_summary(element)},
            })
        return page, element

    def update_element(self, project_id: UUID, element_id: UUID, changes: dict, audit: Audit | None = None) -> tuple[ObjectPage, ObjectElement, int]:
        self._require_project(project_id)
        if "name" in changes:
            validate_name(changes["name"], 100, "Element name")
        if "element_type" in changes:
            validate_element_type(changes["element_type"])
        cleaned = validate_locators(changes["locators"]) if "locators" in changes else None
        captured: dict = {}

        def change(unit: ObjectRepositoryUnit):
            page, element = self._active_element(unit.repository, element_id)
            captured["before"] = {"key": f"{page.name}.{element.name}", "locators": _locator_summary(element)}
            renames = {}
            new_name = changes.get("name", element.name)
            if new_name != element.name:
                self._check_element_unique(page, new_name, exclude=element.id)
                renames = {f"{page.name}.{element.name}": f"{page.name}.{new_name}"}
                element.name = new_name
            if "element_type" in changes:
                element.element_type = changes["element_type"]
            if "description" in changes:
                element.description = changes["description"]
            if cleaned is not None:
                _replace_locators(element, cleaned)
            element.manually_edited = True
            element.health = "healthy"
            element.failure_count = 0
            element.consecutive_failed_runs = 0
            element.updated_at = _now()
            return page, element, self._rewrite_keys(unit, renames)

        page, element, rewritten = self._mutate(project_id, change)
        if audit:
            audit("UPDATE", {
                "entity": "object_element", "id": str(element.id), "key": f"{page.name}.{element.name}",
                "before": captured["before"],
                "after": {"key": f"{page.name}.{element.name}", "locators": _locator_summary(element)},
                "scripts_rewritten": rewritten,
            })
        return page, element, rewritten

    def delete_element(self, project_id: UUID, element_id: UUID, deleted_by: UUID | None = None, audit: Audit | None = None) -> int:
        self._require_project(project_id)

        def change(unit: ObjectRepositoryUnit):
            page, element = self._active_element(unit.repository, element_id)
            key = f"{page.name}.{element.name}"
            element.is_deleted, element.deleted_at, element.deleted_by = True, _now(), deleted_by
            return key, element, self._count_references(unit, [key])

        key, element, references = self._mutate(project_id, change)
        if audit:
            audit("DELETE", {
                "entity": "object_element", "id": str(element.id), "key": key,
                "before": {"locators": _locator_summary(element)}, "after": None,
            })
        return references

    # ----------------------------------------------------------- scripts

    def project_id_for_test_case(self, test_case) -> UUID | None:
        """scenario -> requirement -> project, never a query parameter."""
        repo = self.project_repo
        scenario = repo.get_scenario(test_case.scenario_id)
        if not scenario:
            return None
        for project in repo.list_projects():
            if scenario.requirement_id in project.requirements:
                return project.id
        return None

    @staticmethod
    def locators_by_key(repository: ProjectObjectRepository) -> dict[str, list[dict]]:
        return {
            f"{page.name}.{element.name}": [
                {"strategy": loc.strategy, "value": loc.value, "options": loc.options}
                for loc in sorted(element.locators, key=lambda loc: loc.position)
            ]
            for page in repository.active_pages()
            for element in page.active_elements()
        }

    def renderer(self) -> "ScriptRenderer":
        return ScriptRenderer(self)

    def render_for_test_case(self, test_case, script: str | None = None) -> str | None:
        return self.renderer().render(test_case, script)

    def unknown_keys(self, project_id: UUID, script: str | None) -> list[str]:
        """Keys a script references that are not in the repository, plus run time keys."""
        if not script:
            return []
        keys, dynamic = scripts.find_references(script)
        if not keys and not dynamic:
            return []
        known = self.load(project_id).active_keys()
        return [key for key in keys if key not in known] + dynamic

    # ------------------------------------------------------------ prompt

    def prompt_elements(self, project, test_case) -> list[dict]:
        """Elements of every page the test case visits, ordered and capped (AC-6)."""
        if not project or not project.target_url or self.store is None:
            return []
        repository = self.load(project.id)
        pages = repository.active_pages()
        if not pages:
            return []
        from backend.agents.application_reconnaissance_agent import ApplicationReconnaissanceAgent

        urls = ApplicationReconnaissanceAgent(repo=None)._derive_urls_from_test_cases(project.target_url, [test_case])
        paths = list(dict.fromkeys(urlsplit(url).path or "/" for url in [project.target_url, *urls]))
        matched: list[ObjectPage] = []
        for path in paths:
            for page in pages:
                if page not in matched and pattern_matches(page.url_pattern, path):
                    matched.append(page)
        elements = [
            (page, element)
            for page in matched
            for element in page.active_elements()
            if element.health != "broken"
        ]
        elements.sort(key=lambda pair: _HEALTH_ORDER.get(pair[1].health, 3))
        cap = _settings().max_prompt_elements
        if len(elements) > cap:
            logger.warning(
                "Object repository prompt list cut: project_id=%s test_case_id=%s elements=%s cap=%s",
                project.id, test_case.id, len(elements), cap,
            )
            elements = elements[:cap]
        result = []
        for page, element in elements:
            main = element.locators[0] if element.locators else None
            item = {
                "key": f"{page.name}.{element.name}",
                "type": element.element_type,
                "health": element.health,
            }
            if main is not None:
                item["main_locator"] = {"strategy": main.strategy, "value": main.value, **({"options": main.options} if main.options else {})}
            if element.description:
                item["description"] = element.description[:200]
            result.append(item)
        return result

    def prompt_section(self, project, test_case, framework: str = "playwright") -> str | None:
        try:
            elements = self.prompt_elements(project, test_case)
        except Exception as exc:
            logger.warning("Object repository prompt context unavailable: %s", type(exc).__name__)
            return None
        if not elements:
            return None
        call = "orx(driver, '<page>.<element>')" if framework == "selenium" else "await orx(page, '<page>.<element>')"
        returns = "a WebElement" if framework == "selenium" else "a Playwright Locator"
        example = (
            "el = orx(driver, 'login_page.login_button')\n  el.click()"
            if framework == "selenium"
            else "const btn = await orx(page, 'login_page.login_button');\n  await btn.click();"
        )
        return (
            "\n\nOBJECT REPOSITORY (stored elements of the pages this test visits):\n"
            + json.dumps(elements, ensure_ascii=False, separators=(",", ":"))
            + f"\nLocate every listed element only through {call}, which returns {returns}. "
            "Pass the key as a plain quoted string literal exactly as listed; never build a key at "
            "run time. The platform injects orx before the script runs: do not define, import, or "
            "replace it, and do not repeat a listed element's locator yourself. Example:\n  "
            + example
            + "\nFor anything not listed, use the existing live page evidence and locator rules."
        )

    # ------------------------------------------------------- run feedback

    @staticmethod
    def parse_markers(lines: list[str]) -> list[dict]:
        markers = []
        for line in lines or []:
            match = _MARKER.search(str(line))
            if not match:
                continue
            try:
                data = json.loads(match.group(1))
                key = data["key"]
                index = data["index"]
                tried = data["tried"]
                ok = data["ok"]
                if (
                    not isinstance(key, str) or not scripts.KEY_REFERENCE.match(f"orx(page, '{key}')")
                    or not isinstance(index, int) or not isinstance(tried, int) or not isinstance(ok, bool)
                ):
                    raise ValueError("bad marker fields")
            except (ValueError, KeyError, TypeError):
                logger.warning("Skipping a malformed object repository marker")
                continue
            markers.append({"key": key, "index": index, "tried": tried, "ok": ok})
        return markers

    def apply_run_markers(self, project_id: UUID, execution_id: UUID, lines: list[str]) -> int:
        """Turn one execution's markers into element and locator health (AC-10).

        Returns the number of elements updated. Never raises.
        """
        try:
            markers = self.parse_markers(lines)
            if not markers or self.store is None:
                return 0
            by_key: dict[str, list[dict]] = {}
            for marker in markers:
                by_key.setdefault(marker["key"], []).append(marker)
            execution = str(execution_id)

            def change(unit: ObjectRepositoryUnit) -> int:
                updated = 0
                now = _now()
                for key, key_markers in by_key.items():
                    element = unit.repository.element_by_key(key)
                    if element is None:
                        logger.warning("Skipping a marker for an unknown object repository key: %s", key)
                        continue
                    if execution in element.applied_execution_ids:
                        continue
                    failed = [m for m in key_markers if not m["ok"]]
                    if failed:
                        outcome, worst = "failed", max(failed, key=lambda m: m["tried"])
                    else:
                        worst = max(key_markers, key=lambda m: m["index"])
                        outcome = "backup" if worst["index"] > 0 else "healthy"
                    locators = sorted(element.locators, key=lambda loc: loc.position)
                    if outcome == "failed":
                        for locator in locators[: worst["tried"]]:
                            locator.failure_count += 1
                        element.failure_count += 1
                        element.consecutive_failed_runs += 1
                        element.health = "broken" if element.consecutive_failed_runs >= 2 else "needs_review"
                    else:
                        winner = worst["index"]
                        for locator in locators[:winner]:
                            locator.failure_count += 1
                        if winner < len(locators):
                            locators[winner].success_count += 1
                            locators[winner].last_succeeded_at = now
                        element.consecutive_failed_runs = 0
                        element.health = "healthy" if outcome == "healthy" else "needs_review"
                    last = key_markers[-1]
                    element.last_used_at = now
                    element.last_execution_id = execution_id
                    element.last_locator_index = last["index"] if last["ok"] and 0 <= last["index"] < MAX_LOCATORS else None
                    element.applied_execution_ids = (element.applied_execution_ids + [execution])[-APPLIED_EXECUTION_HISTORY:]
                    element.updated_at = now
                    updated += 1
                return updated

            return self._require_store().mutate(project_id, change)
        except Exception:
            logger.exception("Applying object repository run markers failed: execution_id=%s", execution_id)
            return 0

    # -------------------------------------------------- reconnaissance

    @staticmethod
    def fingerprint(element: dict) -> str | None:
        attributes = element.get("attributes") or {}
        name = " ".join(str(element.get("label") or "").lower().split())
        stable = next(
            (attributes.get(attr) for attr in ("data-testid", "id", "name") if attributes.get(attr)),
            "",
        )
        if not name and not stable:
            return None
        raw = f"{element.get('role') or ''}|{attributes.get('type') or ''}|{name}|{stable}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def element_base_name(element: dict) -> str | None:
        attributes = element.get("attributes") or {}
        for candidate in (
            element.get("label"),
            element.get("placeholder"),
            attributes.get("name"),
            attributes.get("id"),
            attributes.get("data-testid"),
        ):
            slug = slugify(str(candidate or ""))
            if slug:
                return slug if slug[0].isalpha() else f"{element['element_type']}_{slug}"[:100]
        return None

    @staticmethod
    def order_recon_locators(candidates: list[dict]) -> tuple[list[dict], bool]:
        """Drop unmatched locators, put a unique one first, keep at most five.

        Returns (locators, ambiguous) where ambiguous means every locator
        matches more than one element.
        """
        seen = set()
        kept = []
        for candidate in candidates:
            if not str(candidate.get("value") or "").strip() or candidate.get("match_count", 0) < 1:
                continue
            signature = (candidate["strategy"], candidate["value"], json.dumps(candidate.get("options") or {}, sort_keys=True))
            if signature in seen:
                continue
            seen.add(signature)
            kept.append(candidate)
        unique = next((c for c in kept if c.get("match_count") == 1), None)
        if unique is not None and kept[0] is not unique:
            kept.remove(unique)
            kept.insert(0, unique)
        kept = kept[:MAX_LOCATORS]
        ambiguous = bool(kept) and unique is None
        return [
            {"strategy": c["strategy"], "value": c["value"], "options": dict(c.get("options") or {})}
            for c in kept
        ], ambiguous

    def ingest_recon(self, project_id: UUID, page_url: str, recon_elements: list[dict], mark_missing: bool = True) -> dict:
        """Merge one inspected page into the repository (AC-1, AC-2, AC-15).

        A harvest passes mark_missing=False (AC-19): it skips the unseen
        element pass and the last_scanned_at write, and is otherwise the
        same code path as a scan.
        """
        counts = {"added": 0, "updated": 0, "marked_missing": 0}
        if self.store is None:
            return counts
        path = urlsplit(page_url).path or "/"

        def change(unit: ObjectRepositoryUnit) -> dict:
            repository = unit.repository
            now = _now()
            matches = matching_pages(repository.pages, path)
            if matches:
                page = matches[0]
            else:
                pattern = page_pattern_for_path(path)
                if any(p.is_deleted and p.url_pattern == pattern for p in repository.pages):
                    return counts
                literal = [s for s in pattern.split("/") if s and s != "*"]
                base = slugify("_".join(literal), 200) or "home"
                if not base[0].isalpha():
                    base = f"page_{base}"[:200]
                name = _unique_name(base, {p.name for p in repository.active_pages()}, 200)
                page = ObjectPage(project_id=project_id, name=name, url_pattern=pattern)
                repository.pages.append(page)

            by_fingerprint = {e.fingerprint: e for e in page.elements if e.fingerprint}
            taken = {e.name for e in page.active_elements()}
            seen: set[UUID] = set()
            type_counters: dict[str, int] = {}
            for data in recon_elements:
                locators, ambiguous = self.order_recon_locators(data.get("locators") or [])
                if not locators:
                    continue
                fingerprint = self.fingerprint(data)
                existing = by_fingerprint.get(fingerprint) if fingerprint else None
                if existing is not None:
                    if existing.id in seen or existing.is_deleted:
                        continue
                    seen.add(existing.id)
                    # Recon never writes an element a tester edited.
                    if existing.manually_edited:
                        continue
                    changed = _replace_locators(existing, locators)
                    if existing.health == "missing":
                        existing.health = "healthy"
                        changed = True
                    if changed:
                        existing.updated_at = now
                        counts["updated"] += 1
                    continue
                base = self.element_base_name(data)
                if base is None:
                    element_type = data["element_type"]
                    type_counters[element_type] = type_counters.get(element_type, 0) + 1
                    base = f"{element_type}_{type_counters[element_type]}"
                name = _unique_name(base, taken, 100)
                taken.add(name)
                element = ObjectElement(
                    page_id=page.id,
                    name=name,
                    element_type=data["element_type"],
                    fingerprint=fingerprint,
                    health="needs_review" if ambiguous else "healthy",
                    locators=[ObjectLocator(position=i, **loc) for i, loc in enumerate(locators)],
                )
                page.elements.append(element)
                if fingerprint:
                    by_fingerprint[fingerprint] = element
                seen.add(element.id)
                counts["added"] += 1

            if not mark_missing:
                return dict(counts)
            for element in page.active_elements():
                if element.id in seen or element.manually_edited or element.health == "missing":
                    continue
                element.health = "missing"
                element.updated_at = now
                counts["marked_missing"] += 1
            page.last_scanned_at = now
            return dict(counts)

        return self._require_store().mutate(project_id, change)

    def ingest_evidence(self, project_id: UUID, recon_pages: list[dict]) -> dict:
        """Ingest every page from `ApplicationReconnaissanceAgent.pop_object_evidence`."""
        totals = {"pages_scanned": 0, "added": 0, "updated": 0, "marked_missing": 0}
        for recon_page in recon_pages or []:
            result = self.ingest_recon(project_id, recon_page.get("url") or "/", recon_page.get("elements") or [])
            totals["pages_scanned"] += 1
            for key in ("added", "updated", "marked_missing"):
                totals[key] += result[key]
        return totals


class ScriptRenderer:
    """Renders stored scripts, caching the project and repository per request."""

    def __init__(self, service: ObjectRepositoryService):
        self.service = service
        self._repositories: dict[UUID, dict[str, list[dict]]] = {}
        self._timeout = None

    def _locators(self, project_id: UUID) -> dict[str, list[dict]]:
        if project_id not in self._repositories:
            self._repositories[project_id] = self.service.locators_by_key(self.service.load(project_id))
        return self._repositories[project_id]

    def render(self, test_case, script: str | None = None) -> str | None:
        script = test_case.playwright_script if script is None else script
        if not script or "orx(" not in script:
            return script
        try:
            if self.service.store is None:
                return script
            project_id = self.service.project_id_for_test_case(test_case)
            if project_id is None:
                return script
            if self._timeout is None:
                self._timeout = _settings().locator_timeout_ms
            return scripts.render_script(script, self._locators(project_id), self._timeout)
        except Exception:
            logger.exception("Rendering the object repository block failed: test_case_id=%s", getattr(test_case, "id", None))
            return script
