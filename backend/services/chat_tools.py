"""
Read tools for the chat assistant (spec 0005, tool catalog).

Every tool is scoped to one project. Any ID an argument names is checked to
belong to that project first; anything else is "not found in this project",
so a tool can never read across projects. No tool returns credentials,
vault data, passwords, or API keys.

`ChatToolbox.run` always returns a JSON string no longer than the result
cap: list rows are dropped from the end (with `truncated` and `total`), and
a single record that is still too long has its longest text fields cut.
"""

import json
import logging
import re
from typing import Callable
from uuid import UUID

from backend.services.dashboard_metrics import compute_dashboard_metrics

logger = logging.getLogger(__name__)

LIST_DEFAULT = 20
LIST_MAX = 50
SCRIPT_CHARS = 2000
TRUNCATED = "…[truncated]"
NOT_FOUND = "not found in this project"
_SECRET_KEY = re.compile(r"password|passwd|token|secret|key", re.IGNORECASE)

# Short labels the panel shows while a tool runs ("Looking up test cases…").
TOOL_LABELS = {
    "get_project_overview": "the project overview",
    "list_requirements": "requirements",
    "get_requirement": "a requirement",
    "list_scenarios": "scenarios",
    "list_test_cases": "test cases",
    "get_test_case": "a test case",
    "list_executions": "executions",
    "get_execution": "an execution",
    "get_dashboard_metrics": "dashboard metrics",
    "list_object_repository_pages": "object repository pages",
}


class ToolNotFound(Exception):
    """An ID that is not in this project, or does not exist."""


def _id(arguments: dict, name: str, required: bool = True) -> UUID | None:
    value = arguments.get(name)
    if value in (None, ""):
        if required:
            raise ValueError(f"{name} is required")
        return None
    try:
        return UUID(str(value))
    except ValueError:
        raise ToolNotFound(name) from None


def _limit(arguments: dict) -> int:
    try:
        value = int(arguments.get("limit") or LIST_DEFAULT)
    except (TypeError, ValueError):
        value = LIST_DEFAULT
    return max(1, min(value, LIST_MAX))


def _query(arguments: dict) -> str:
    value = arguments.get("query")
    return value.strip().lower() if isinstance(value, str) else ""


def _value(value):
    return value.value if hasattr(value, "value") else value


def _redact(data):
    """Test data values under secret looking keys become "[redacted]"."""
    if isinstance(data, dict):
        return {k: ("[redacted]" if _SECRET_KEY.search(str(k)) else _redact(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [_redact(item) for item in data]
    return data


def _scenario_status(scenario) -> str:
    if scenario.approved:
        return "approved"
    if getattr(scenario, "rejected", False):
        return "rejected"
    return "pending review"


def _schema(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties or {}, "required": required or []},
        },
    }


_LIMIT = {"type": "integer", "minimum": 1, "maximum": LIST_MAX, "description": f"Rows to return (default {LIST_DEFAULT}, at most {LIST_MAX})."}
_UUID = {"type": "string", "description": "UUID"}

TOOL_DEFINITIONS = [
    _schema("get_project_overview", "The project's name, target URL, framework, and counts of requirements, scenarios, test cases, and executions."),
    _schema(
        "list_requirements",
        "List the project's requirements: id, title, priority, and counts of scenarios and test cases.",
        {"query": {"type": "string", "description": "Text to search in the title or description."}, "limit": _LIMIT},
    ),
    _schema("get_requirement", "A full requirement and its scenario IDs.", {"requirement_id": _UUID}, ["requirement_id"]),
    _schema(
        "list_scenarios",
        "List scenarios (id, title, requirement ID, review status), optionally for one requirement.",
        {"requirement_id": _UUID, "limit": _LIMIT},
    ),
    _schema(
        "list_test_cases",
        "List test cases (id, title, whether a script exists, last execution status), optionally filtered.",
        {"scenario_id": _UUID, "requirement_id": _UUID, "query": {"type": "string", "description": "Text to search in the title."}, "limit": _LIMIT},
    ),
    _schema("get_test_case", "A test case's steps, expected result, test data, and script (cut to 2000 characters).", {"test_case_id": _UUID}, ["test_case_id"]),
    _schema(
        "list_executions",
        "List executions, newest first: id, test case, status, start time, duration.",
        {"status": {"type": "string", "enum": ["passed", "failed", "skipped", "error"]}, "test_case_id": _UUID, "limit": _LIMIT},
    ),
    _schema("get_execution", "One execution: status, error message, failed step, analysis summary, and which artifacts exist.", {"execution_id": _UUID}, ["execution_id"]),
    _schema("get_dashboard_metrics", "The project's dashboard numbers: totals, pass ratio, coverage, approval and defect counts, recent executions."),
    _schema("list_object_repository_pages", "List the project's object repository pages and their element counts.", {"limit": _LIMIT}),
]

READ_TOOL_NAMES = frozenset(tool["function"]["name"] for tool in TOOL_DEFINITIONS)


def fit_result(payload: dict, max_chars: int) -> str:
    """JSON no longer than max_chars, never cut mid JSON (spec 0005)."""
    text = json.dumps(payload, default=str, ensure_ascii=False)
    if len(text) <= max_chars:
        return text

    items = payload.get("items")
    if isinstance(items, list):
        total = payload.get("total", len(items))
        kept = list(items)
        while kept:
            kept.pop()
            candidate = {**payload, "items": kept, "truncated": True, "total": total}
            text = json.dumps(candidate, default=str, ensure_ascii=False)
            if len(text) <= max_chars:
                return text
        payload = {**payload, "items": [], "truncated": True, "total": total}

    # A single record still too long: cut its longest text fields, longest first.
    record = json.loads(json.dumps(payload, default=str, ensure_ascii=False))
    for _ in range(200):
        text = json.dumps(record, ensure_ascii=False)
        excess = len(text) - max_chars
        if excess <= 0:
            return text
        path, longest = _longest_string(record)
        if path is None or len(longest) <= len(TRUNCATED) + 20:
            break
        keep = max(20, len(longest) - excess - len(TRUNCATED) - 8)
        if keep >= len(longest) - len(TRUNCATED):
            keep = len(longest) // 2
        _set_path(record, path, longest[:keep] + TRUNCATED)
    return json.dumps({"error": "result too large", "truncated": True})


def _longest_string(node, path=()) -> tuple[tuple | None, str]:
    best: tuple[tuple | None, str] = (None, "")
    children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for key, child in children:
        if isinstance(child, str):
            candidate = (path + (key,), child)
        else:
            candidate = _longest_string(child, path + (key,))
        if candidate[0] is not None and len(candidate[1]) > len(best[1]):
            best = candidate
    return best


def _set_path(node, path: tuple, value) -> None:
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


class ChatToolbox:
    """Runs the read tools against the project repository. Tests pass a fake
    repository and object repository store."""

    def __init__(self, project_repository=None, object_repository_store=None, result_chars: int = 4000):
        self._repo = project_repository
        self._object_store = object_repository_store
        self.result_chars = result_chars
        self._tools: dict[str, Callable] = {name: getattr(self, f"_{name}") for name in READ_TOOL_NAMES}

    @property
    def repo(self):
        if self._repo is None:
            from backend.repository.project_repository import get_project_repository

            return get_project_repository()
        return self._repo

    def definitions(self) -> list[dict]:
        return TOOL_DEFINITIONS

    def run(self, project, name: str, arguments: dict | None) -> str:
        """The tool's result as JSON within the result cap. Errors come back as
        `{"error": ...}` so the model can read them and carry on."""
        if name not in self._tools:
            return json.dumps({"error": f"unknown tool {name}"})
        if arguments is None:
            return json.dumps({"error": "arguments were not valid JSON"})
        try:
            payload = self._tools[name](project, arguments)
        except ToolNotFound:
            payload = {"error": NOT_FOUND}
        except ValueError as exc:
            payload = {"error": str(exc)}
        except Exception:
            logger.exception("Chat tool %s failed", name)
            payload = {"error": "the lookup failed"}
        return fit_result(payload, self.result_chars)

    # ------------------------------------------------------------ scoping

    def _requirement_ids(self, project) -> set[str]:
        return {str(r) for r in (getattr(project, "requirements", None) or [])}

    def _scenario(self, project, scenario_id: UUID):
        scenario = self.repo.get_scenario(scenario_id)
        if scenario is None or str(scenario.requirement_id) not in self._requirement_ids(project):
            raise ToolNotFound(str(scenario_id))
        return scenario

    def _test_case(self, project, test_case_id: UUID):
        test_case = self.repo.get_test_case(test_case_id)
        if test_case is None:
            raise ToolNotFound(str(test_case_id))
        self._scenario(project, test_case.scenario_id)
        return test_case

    def _check_requirement(self, project, requirement_id: UUID) -> None:
        if str(requirement_id) not in self._requirement_ids(project):
            raise ToolNotFound(str(requirement_id))

    def _executions(self, project) -> list:
        return sorted(self.repo.get_execution_results(project.id), key=lambda e: e.executed_at, reverse=True)

    @staticmethod
    def _latest_status(executions: list) -> dict[str, str]:
        """test case ID -> status of its newest execution (executions newest first)."""
        latest: dict[str, str] = {}
        for execution in executions:
            latest.setdefault(str(execution.test_case_id), _value(execution.status))
        return latest

    # -------------------------------------------------------------- tools

    def _get_project_overview(self, project, arguments: dict) -> dict:
        # An explicit allow list of fields: never the username, password, or vault data.
        return {
            "name": project.name,
            "description": getattr(project, "description", ""),
            "target_url": getattr(project, "target_url", None),
            "framework": getattr(project, "framework", None),
            "line_of_business": getattr(project, "line_of_business", None),
            "jira_project_key": getattr(project, "jira_project_key", None),
            "counts": {
                "requirements": len(self.repo.get_requirements(project.id)),
                "scenarios": len(self.repo.get_scenarios_for_project(project.id)),
                "test_cases": len(self.repo.get_test_cases_for_project(project.id)),
                "executions": len(self.repo.get_execution_results(project.id)),
            },
        }

    def _list_requirements(self, project, arguments: dict) -> dict:
        query = _query(arguments)
        requirements = self.repo.get_requirements(project.id)
        if query:
            requirements = [
                r for r in requirements
                if query in r.title.lower() or query in (r.description or "").lower() or query in (r.requirement_id or "").lower()
            ]
        scenarios = self.repo.get_scenarios_for_project(project.id)
        test_cases = self.repo.get_test_cases_for_project(project.id)
        requirement_of_scenario = {str(s.id): str(s.requirement_id) for s in scenarios}
        scenario_counts: dict[str, int] = {}
        for scenario in scenarios:
            key = str(scenario.requirement_id)
            scenario_counts[key] = scenario_counts.get(key, 0) + 1
        test_case_counts: dict[str, int] = {}
        for test_case in test_cases:
            key = requirement_of_scenario.get(str(test_case.scenario_id))
            if key:
                test_case_counts[key] = test_case_counts.get(key, 0) + 1
        rows = [
            {
                "id": str(r.id),
                "ref": r.requirement_id,
                "title": r.title,
                "priority": _value(r.priority),
                "scenarios": scenario_counts.get(str(r.id), 0),
                "test_cases": test_case_counts.get(str(r.id), 0),
            }
            for r in requirements
        ]
        return {"items": rows[: _limit(arguments)], "total": len(rows)}

    def _get_requirement(self, project, arguments: dict) -> dict:
        requirement_id = _id(arguments, "requirement_id")
        self._check_requirement(project, requirement_id)
        requirement = self.repo.get_requirement(requirement_id)
        if requirement is None:
            raise ToolNotFound(str(requirement_id))
        data = requirement.model_dump(mode="json", exclude={"backlog", "attachments"})
        data["scenario_ids"] = [str(s.id) for s in self.repo.get_scenarios(requirement_id)]
        return data

    def _list_scenarios(self, project, arguments: dict) -> dict:
        requirement_id = _id(arguments, "requirement_id", required=False)
        if requirement_id is not None:
            self._check_requirement(project, requirement_id)
            scenarios = self.repo.get_scenarios(requirement_id)
        else:
            scenarios = self.repo.get_scenarios_for_project(project.id)
        rows = [
            {
                "id": str(s.id),
                "ref": s.scenario_ref_id,
                "title": s.scenario_name,
                "requirement_id": str(s.requirement_id),
                "status": _scenario_status(s),
            }
            for s in scenarios
        ]
        return {"items": rows[: _limit(arguments)], "total": len(rows)}

    def _list_test_cases(self, project, arguments: dict) -> dict:
        scenario_id = _id(arguments, "scenario_id", required=False)
        requirement_id = _id(arguments, "requirement_id", required=False)
        if scenario_id is not None:
            self._scenario(project, scenario_id)
            test_cases = self.repo.get_test_cases(scenario_id)
        else:
            test_cases = self.repo.get_test_cases_for_project(project.id)
        if requirement_id is not None:
            self._check_requirement(project, requirement_id)
            scenario_ids = {str(s.id) for s in self.repo.get_scenarios(requirement_id)}
            test_cases = [tc for tc in test_cases if str(tc.scenario_id) in scenario_ids]
        query = _query(arguments)
        if query:
            test_cases = [tc for tc in test_cases if query in tc.title.lower() or query in (tc.test_case_ref_id or "").lower()]
        latest = self._latest_status(self._executions(project))
        rows = [
            {
                "id": str(tc.id),
                "ref": tc.test_case_ref_id,
                "title": tc.title,
                "scenario_id": str(tc.scenario_id),
                "status": _value(tc.status),
                "has_script": bool(tc.playwright_script),
                "last_execution_status": latest.get(str(tc.id)),
            }
            for tc in test_cases
        ]
        return {"items": rows[: _limit(arguments)], "total": len(rows)}

    def _get_test_case(self, project, arguments: dict) -> dict:
        test_case = self._test_case(project, _id(arguments, "test_case_id"))
        script = test_case.playwright_script or ""
        latest = self._latest_status(self._executions(project))
        return {
            "id": str(test_case.id),
            "ref": test_case.test_case_ref_id,
            "title": test_case.title,
            "scenario_id": str(test_case.scenario_id),
            "priority": _value(test_case.priority),
            "status": _value(test_case.status),
            "evaluation_status": _value(test_case.evaluation_status),
            "preconditions": test_case.preconditions,
            "steps": test_case.steps,
            "expected_result": test_case.expected_result,
            "test_data": _redact(test_case.test_data),
            "test_data_sets": _redact(test_case.test_data_sets),
            "has_script": bool(script),
            "script": (script[:SCRIPT_CHARS] + TRUNCATED) if len(script) > SCRIPT_CHARS else script or None,
            "script_validation_status": _value(test_case.script_validation_status),
            "last_execution_status": latest.get(str(test_case.id)),
        }

    def _list_executions(self, project, arguments: dict) -> dict:
        executions = self._executions(project)
        status = arguments.get("status")
        if isinstance(status, str) and status:
            executions = [e for e in executions if _value(e.status) == status.lower()]
        test_case_id = _id(arguments, "test_case_id", required=False)
        if test_case_id is not None:
            self._test_case(project, test_case_id)
            executions = [e for e in executions if e.test_case_id == test_case_id]
        titles = {str(tc.id): tc.title for tc in self.repo.get_test_cases_for_project(project.id)}
        rows = [
            {
                "id": str(e.id),
                "test_case_id": str(e.test_case_id),
                "test_case": titles.get(str(e.test_case_id)),
                "status": _value(e.status),
                "started": e.executed_at.isoformat() if e.executed_at else None,
                "duration_seconds": e.duration_seconds,
            }
            for e in executions
        ]
        return {"items": rows[: _limit(arguments)], "total": len(rows)}

    def _get_execution(self, project, arguments: dict) -> dict:
        execution_id = _id(arguments, "execution_id")
        execution = self.repo.get_execution_result(project.id, execution_id)
        if execution is None:
            raise ToolNotFound(str(execution_id))
        test_case = self.repo.get_test_case(execution.test_case_id)
        failed_step = next(
            (entry.get("event") for entry in (execution.timeline or []) if isinstance(entry, dict) and entry.get("type") == "error"),
            None,
        )
        return {
            "id": str(execution.id),
            "test_case_id": str(execution.test_case_id),
            "test_case": test_case.title if test_case else None,
            "status": _value(execution.status),
            "started": execution.executed_at.isoformat() if execution.executed_at else None,
            "duration_seconds": execution.duration_seconds,
            "error_message": execution.error_message,
            "failed_step": failed_step,
            "analysis": {
                "failure_category": execution.failure_category,
                "root_cause_summary": execution.root_cause_summary,
                "suggest_retry": execution.suggest_retry,
            },
            "jira_bug_id": execution.jira_bug_id,
            "artifacts": {
                "screenshot": bool(execution.screenshot_path or execution.screenshots),
                "video": bool(execution.video_path),
                "trace": bool(execution.trace_path),
            },
        }

    def _get_dashboard_metrics(self, project, arguments: dict) -> dict:
        return compute_dashboard_metrics(self.repo, project.id)

    def _list_object_repository_pages(self, project, arguments: dict) -> dict:
        store = self._object_store
        if store is None:
            from backend.repository.object_repository_store import get_object_repository_store

            store = get_object_repository_store(self.repo)
        if store is None:
            return {"items": [], "total": 0}
        pages = store.load(project.id).active_pages()
        rows = [{"id": str(p.id), "name": p.name, "url_pattern": p.url_pattern, "elements": len(p.active_elements())} for p in pages]
        return {"items": rows[: _limit(arguments)], "total": len(rows)}
