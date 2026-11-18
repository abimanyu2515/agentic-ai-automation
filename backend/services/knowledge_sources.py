"""Read a project source again and build the text to index (spec 0006, AC-14).

The knowledge indexer calls `read_source` when a job runs, so it always
indexes current content. It returns a `SourceText`, or None when the source is
gone, soft deleted, not in this project, or no longer qualifies (a test case
that is not approved, an execution that did not fail). None means the
indexer removes the entry.

Formatters per source type (the Value sourcing table of spec 0006):
- test_case: title, preconditions, steps, and expected result
- jira_story: story key, summary, description, and acceptance criteria
- execution_failure: test case title, error, failure category, and analysis summary
- locator: page name and URL pattern, with each element's name and locators
- defect: the Jira bug's key, summary, status, priority, and description,
  read from Jira through an injected `JiraService` (AC-14a)

A defect has no local record: it belongs to a project only while a live
execution (`jira_bug_id`) or live test case (`jira_issue_key`) of that project
links its key. A Jira read that fails, or a `JiraService` in mock mode, raises
`SourceUnavailable` instead of returning None, so it never removes an entry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

logger = logging.getLogger(__name__)

# Source types the indexer can build.
INDEXABLE_SOURCE_TYPES = ("test_case", "jira_story", "execution_failure", "defect", "locator")
FAILED_EXECUTION_STATUSES = {"failed", "error"}
DEFECT_DESCRIPTION_LIMIT = 4000


class SourceUnavailable(Exception):
    """The source exists but cannot be read now (AC-14a). A transient one is
    retried on the indexer's retry delays; any other is skipped. Never a removal."""

    def __init__(self, message: str, transient: bool):
        super().__init__(message)
        self.transient = transient


@dataclass
class SourceText:
    title: str
    text: str


def _value(x) -> str:
    return str(getattr(x, "value", x) or "")


def _lines(heading: str, items) -> list[str]:
    items = [str(i).strip() for i in (items or []) if str(i).strip()]
    if not items:
        return []
    return [f"{heading}:"] + [f"{n}. {item}" for n, item in enumerate(items, 1)]


def _uuid(ref: str) -> UUID | None:
    try:
        return UUID(str(ref))
    except (TypeError, ValueError):
        return None


def _test_case_in_project(repo, project, test_case) -> bool:
    """scenario -> requirement -> project, the same chain the object repository uses."""
    scenario = repo.get_scenario(test_case.scenario_id)
    return bool(scenario and scenario.requirement_id in (project.requirements or []))


def read_test_case(repo, project, ref: str) -> SourceText | None:
    test_case_id = _uuid(ref)
    test_case = repo.get_test_case(test_case_id) if test_case_id else None
    if test_case is None or getattr(test_case, "is_deleted", False):
        return None
    if _value(test_case.evaluation_status) != "approved":
        return None
    if not _test_case_in_project(repo, project, test_case):
        return None
    parts = [f"Test case: {test_case.title}"]
    if test_case.test_case_ref_id:
        parts.append(f"Reference: {test_case.test_case_ref_id}")
    parts += _lines("Preconditions", test_case.preconditions)
    parts += _lines("Steps", test_case.steps)
    parts.append(f"Expected result: {test_case.expected_result}")
    return SourceText(title=test_case.title, text="\n".join(parts))


def read_jira_story(repo, project, ref: str) -> SourceText | None:
    story = next((r for r in repo.get_requirements(project.id) if r.jira_issue_key == ref), None)
    if story is None:
        return None
    parts = [f"Jira story {ref}: {story.title}"]
    if story.description:
        parts += ["Description:", story.description.strip()]
    parts += _lines("Acceptance criteria", story.acceptance_criteria)
    return SourceText(title=story.title, text="\n".join(parts))


def read_execution_failure(repo, project, ref: str) -> SourceText | None:
    execution_id = _uuid(ref)
    result = repo.get_execution_result(project.id, execution_id) if execution_id else None
    if result is None or _value(result.status) not in FAILED_EXECUTION_STATUSES:
        return None
    if repo.is_execution_deleted(project.id, execution_id):
        return None
    test_case = repo.get_test_case(result.test_case_id, include_deleted=True)
    case_title = test_case.title if test_case else f"Test case {result.test_case_id}"
    title = f"{case_title} failed"
    parts = [f"Failed execution of: {case_title}", f"Status: {_value(result.status)}"]
    if result.error_message:
        parts.append(f"Error: {result.error_message.strip()}")
    if result.failure_category:
        parts.append(f"Failure category: {result.failure_category}")
    if result.root_cause_summary:
        parts.append(f"Analysis summary: {result.root_cause_summary.strip()}")
    return SourceText(title=title, text="\n".join(parts))


def read_locator_page(project, ref: str, object_repository_service=None) -> SourceText | None:
    page_id = _uuid(ref)
    if page_id is None:
        return None
    if object_repository_service is None:
        from backend.services.object_repository_service import ObjectRepositoryService

        object_repository_service = ObjectRepositoryService()
    page = object_repository_service.load(project.id).find_page(page_id)
    if page is None or page.is_deleted:
        return None
    parts = [f"Object repository page: {page.name}", f"URL pattern: {page.url_pattern}"]
    elements = page.active_elements()
    if elements:
        parts.append("Elements:")
    for element in elements:
        locators = ", ".join(f"{loc.strategy}={loc.value}" for loc in sorted(element.locators, key=lambda l: l.position))
        line = f"- {page.name}.{element.name} ({element.element_type})"
        if element.description:
            line += f": {element.description}"
        if locators:
            line += f" | locators: {locators}"
        parts.append(line)
    return SourceText(title=page.name, text="\n".join(parts))


def defect_key(value) -> str | None:
    """A bug key trimmed for exact, case sensitive matching; None when empty (AC-14a)."""
    key = str(value or "").strip()
    return key or None


def project_defect_keys(repo, project) -> list[str]:
    """Distinct bug keys linked from the project's live executions and test cases."""
    keys: list[str] = []
    for result in repo.get_execution_results(project.id):
        key = defect_key(getattr(result, "jira_bug_id", None))
        if key and not repo.is_execution_deleted(project.id, result.id):
            keys.append(key)
    for test_case in repo.get_test_cases_for_project(project.id):
        key = defect_key(getattr(test_case, "jira_issue_key", None))
        if key:
            keys.append(key)
    return list(dict.fromkeys(keys))


def defect_linked(repo, project, key: str) -> bool:
    return defect_key(key) in project_defect_keys(repo, project)


def _field_text(value) -> str:
    """A Jira field as plain text: a None or missing value is empty, a string is
    used as is, and rich text (Atlassian document format) has its node texts joined."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    texts: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("text"), str):
                texts.append(node["text"])
            for child in node.get("content") or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return " ".join(t.strip() for t in texts if t and t.strip())


def _named(value) -> str:
    return _field_text(value.get("name")) if isinstance(value, dict) else ""


def defect_text(key: str, issue: dict) -> SourceText:
    fields = (issue or {}).get("fields") or {}
    summary = _field_text(fields.get("summary")).strip()
    description = _field_text(fields.get("description")).strip()[:DEFECT_DESCRIPTION_LIMIT]
    lines = [f"Defect {key}: {summary}" if summary else f"Defect {key}"]
    status = _named(fields.get("status")).strip()
    priority = _named(fields.get("priority")).strip()
    if status:
        lines.append(f"Status: {status}")
    if priority:
        lines.append(f"Priority: {priority}")
    if description:
        lines += ["Description:", description]
    return SourceText(title=summary or key, text="\n".join(lines))


def read_defect(repo, project, ref: str, jira_service=None) -> SourceText | None:
    """AC-14a. The link check runs first, so an unlinked key is removed even
    when Jira is down. Mock mode is checked before any Jira call."""
    key = defect_key(ref)
    if key is None or not defect_linked(repo, project, key):
        return None
    if jira_service is None:
        from backend.services.jira_service import JiraService

        jira_service = JiraService()
    if getattr(jira_service, "mock_mode", False):
        raise SourceUnavailable("Jira is not configured, defects are skipped", transient=False)
    try:
        issue = jira_service.get_issue(key)
    except Exception as exc:
        raise SourceUnavailable(f"could not read {key} from Jira: {exc}", transient=True) from exc
    if not issue:
        # get_issue returns None for a network error too, so it is never read as "gone".
        raise SourceUnavailable(f"could not read {key} from Jira", transient=True)
    return defect_text(key, issue)


def read_source(repo, project, source_type: str, ref: str, object_repository_service=None, jira_service=None) -> SourceText | None:
    if source_type == "defect":
        return read_defect(repo, project, ref, jira_service)
    if source_type == "test_case":
        return read_test_case(repo, project, ref)
    if source_type == "jira_story":
        return read_jira_story(repo, project, ref)
    if source_type == "execution_failure":
        return read_execution_failure(repo, project, ref)
    if source_type == "locator":
        return read_locator_page(project, ref, object_repository_service)
    raise ValueError(f"source type {source_type!r} cannot be indexed")


def list_project_sources(repo, project, object_repository_service=None) -> list[tuple[str, str]]:
    """Every current (source_type, ref) of a project, for the reindex script (AC-16).
    Soft deleted items are skipped; the indexer filters what no longer qualifies."""
    sources: list[tuple[str, str]] = []
    for test_case in repo.get_test_cases_for_project(project.id):
        if _value(test_case.evaluation_status) == "approved":
            sources.append(("test_case", str(test_case.id)))
    for requirement in repo.get_requirements(project.id):
        if requirement.jira_issue_key:
            sources.append(("jira_story", requirement.jira_issue_key))
    for result in repo.get_execution_results(project.id):
        if _value(result.status) in FAILED_EXECUTION_STATUSES and not repo.is_execution_deleted(project.id, result.id):
            sources.append(("execution_failure", str(result.id)))
    sources += [("defect", key) for key in project_defect_keys(repo, project)]
    if object_repository_service is None:
        from backend.services.object_repository_service import ObjectRepositoryService

        object_repository_service = ObjectRepositoryService()
    try:
        for page in object_repository_service.load(project.id).active_pages():
            sources.append(("locator", str(page.id)))
    except Exception:
        logger.exception("Could not list object repository pages for project %s", project.id)
    return list(dict.fromkeys(sources))
