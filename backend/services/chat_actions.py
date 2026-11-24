"""
Chat assistant actions (spec 0005, action catalog).

The model can only *propose* an action, through a `propose_<type>` tool. The
proposal is validated here, its target names are read from the repository,
and the server fills in `danger`, `permission`, and `affects`. Nothing runs
until the user confirms; confirm runs the saved `params`, never anything the
browser sends.

`ChatActionExecutor` starts confirmed `inline` actions in a worker thread.
The worker moves the action to its final status through
`compare_and_set_action_status` and writes the final audit entry. Every
dependency comes through the constructor so tests pass fakes and a runner
that runs the work in the calling thread.

The complete action catalog is declared below; all mutations still require
an explicit user confirmation before they execute.
"""

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.models.common import Priority
from backend.models.test_case import EvaluationStatus
from backend.schemas.scenario_dto import ScenarioUpdate
from backend.schemas.testcase_dto import TestCaseUpdate
from backend.services.knowledge_indexer import queue_defect_keys, queue_knowledge_source, queue_test_case_removals

from backend.models.chat import utc_now

logger = logging.getLogger(__name__)

PROPOSE_PREFIX = "propose_"
SUMMARY_MAX = 200
CHANGED_MESSAGE = "This item changed or no longer exists, ask again"
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "expired"})
ACTIVE = frozenset({"queued", "running"})
RESTART_ERROR = "Interrupted by a server restart"
LOST_ERROR = "The job was lost on restart, ask again"
FAILED_EXECUTION_STATUSES = frozenset({"failed", "error"})
MAX_FAILED_EXECUTIONS = 200
FAILED_TARGETS_SHOWN = 20


class ActionInvalid(ValueError):
    """The proposal or its saved params no longer pass validation."""


# ---------------------------------------------------------------- params models


class GenerateScenariosParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: UUID = Field(description="The requirement to generate scenarios for.")
    count: int = Field(default=3, ge=1, le=10, description="How many scenarios to generate (1 to 10).")
    mode: Literal["append", "replace"] = Field(
        default="append",
        description="append keeps the existing scenarios; replace deletes them after the new ones are generated.",
    )


class GenerateTestCasesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: UUID


class GenerateScriptParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_id: UUID
    framework: str = "Playwright"


class RunTestCaseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_id: UUID


class RunBatchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_ids: list[UUID] = Field(min_length=1, max_length=50)


class CreateRequirementParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    priority: str = "medium"
    business_domain: str = "general"


class RequirementChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1)
    priority: str | None = None
    business_domain: str | None = None


class UpdateRequirementParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: UUID
    changes: RequirementChanges


class ScenarioChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1)
    priority: Priority | None = None


class UpdateScenarioParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: UUID
    changes: ScenarioChanges


class UpdateTestCaseChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    steps: list[str] | None = Field(default=None, min_length=1)
    expected_result: str | None = Field(default=None, min_length=1)
    data: dict[str, str] | None = None


class UpdateTestCaseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_id: UUID
    changes: UpdateTestCaseChanges


class ReviewTestCaseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_id: UUID
    decision: Literal["approved", "rejected"] = Field(
        description="approved when the user asks to approve the test case, rejected when they ask to reject it.",
    )


class GenerateTestDataParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_id: UUID


class DeleteRequirementParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: UUID


class ReviewScenarioParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: UUID
    decision: Literal["approved", "rejected"] = Field(
        description="approved when the user asks to approve the scenario, rejected when they ask to reject it.",
    )


class DeleteScenarioParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: UUID


class DeleteTestCaseParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_case_id: UUID


class DeleteExecutionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: UUID


class DeleteFailedExecutionsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_ids: list[UUID] = Field(default_factory=list)


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportBugParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: UUID


class SubmitBugReportParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bug_report_id: UUID


# ---------------------------------------------------------------------- catalog


@dataclass(frozen=True)
class ActionSpec:
    type: str
    description: str
    params_model: type[BaseModel]
    module: str
    permission_action: str
    affects: tuple[str, ...]
    job_kind: str = "inline"
    # Params the server fills at propose time; the model never sends them.
    server_fields: frozenset[str] = field(default_factory=frozenset)

    @property
    def tool_name(self) -> str:
        return f"{PROPOSE_PREFIX}{self.type}"


ACTION_CATALOG: dict[str, ActionSpec] = {
    spec.type: spec
    for spec in (
        ActionSpec(
            type="generate_scenarios",
            description=(
                "Propose generating test scenarios for one requirement. This does not run anything: "
                "the user sees a card and must click Run."
            ),
            params_model=GenerateScenariosParams,
            module="Scenarios",
            permission_action="create",
            affects=("scenarios",),
            server_fields=frozenset({"replace_ids"}),
        ),
        ActionSpec(
            "generate_test_cases", "Propose generating test cases for one requirement.", GenerateTestCasesParams,
            "TestCases", "create", ("testcases",),
        ),
        ActionSpec(
            "generate_script", "Propose generating a Playwright script for one test case.", GenerateScriptParams,
            "Playwright", "create", ("playwright", "testcases"), "script_job",
        ),
        ActionSpec(
            "run_test_case", "Propose running one test case.", RunTestCaseParams,
            "Executions", "create", ("executions", "reports"), "execution",
        ),
        ActionSpec(
            "run_batch", "Propose running a selected set of test cases.", RunBatchParams,
            "Executions", "create", ("executions", "reports"), "batch",
        ),
        ActionSpec(
            "create_requirement", "Propose creating a requirement.", CreateRequirementParams,
            "Requirements", "create", ("requirements",),
        ),
        ActionSpec(
            "update_requirement", "Propose updating selected requirement fields.", UpdateRequirementParams,
            "Requirements", "edit", ("requirements",), server_fields=frozenset({"old_values"}),
        ),
        ActionSpec(
            "delete_requirement", "Propose deleting a requirement and its scenarios and test cases.",
            DeleteRequirementParams, "Requirements", "delete", ("requirements",),
            server_fields=frozenset({"scenario_ids", "test_case_ids"}),
        ),
        ActionSpec(
            "update_scenario", "Propose updating selected scenario fields.", UpdateScenarioParams,
            "Scenarios", "edit", ("scenarios",), server_fields=frozenset({"old_values"}),
        ),
        ActionSpec(
            "review_scenario",
            "Propose approving or rejecting one scenario. Rejecting deletes the scenario's test cases.",
            ReviewScenarioParams, "Scenarios", "edit", ("scenarios", "testcases"),
        ),
        ActionSpec(
            "duplicate_scenario", "Propose duplicating a scenario.", DeleteScenarioParams,
            "Scenarios", "create", ("scenarios",),
        ),
        ActionSpec(
            "delete_scenario", "Propose deleting a scenario.", DeleteScenarioParams,
            "Scenarios", "delete", ("scenarios",),
        ),
        ActionSpec(
            "update_test_case", "Propose updating selected test case fields.", UpdateTestCaseParams,
            "TestCases", "edit", ("testcases",), server_fields=frozenset({"old_values"}),
        ),
        ActionSpec(
            "review_test_case", "Propose approving or rejecting one test case.", ReviewTestCaseParams,
            "TestCases", "edit", ("testcases",),
        ),
        ActionSpec(
            "generate_test_data",
            "Propose generating test data (20 datasets) for one Playwright test case. "
            "Any script that no longer matches the new data is cleared.",
            GenerateTestDataParams, "TestCases", "edit", ("testcases", "playwright"),
        ),
        ActionSpec(
            "delete_test_case", "Propose deleting a test case.", DeleteTestCaseParams,
            "TestCases", "delete", ("testcases",),
        ),
        ActionSpec(
            "delete_execution", "Propose deleting one execution.", DeleteExecutionParams,
            "Executions", "delete", ("executions", "reports"),
        ),
        ActionSpec(
            "delete_failed_executions", "Propose deleting the project's currently failed executions.",
            DeleteFailedExecutionsParams, "Executions", "delete", ("executions", "reports"),
            server_fields=frozenset({"execution_ids"}),
        ),
        ActionSpec(
            "report_bug",
            "Propose drafting a bug report for one failed or errored execution. The draft stays in the "
            "Bug Reports view for review; nothing is sent to Jira.",
            ReportBugParams, "Executions", "edit", ("executions", "bugs"),
        ),
        ActionSpec(
            "submit_bug_report",
            "Propose submitting one draft bug report to Jira. Jira is searched for a duplicate first; "
            "a duplicate gets a comment, otherwise a new Bug is created.",
            SubmitBugReportParams, "Executions", "edit", ("bugs", "executions"),
        ),
    )
}


def action_type_for_tool(name: str) -> str | None:
    if not name.startswith(PROPOSE_PREFIX):
        return None
    action_type = name[len(PROPOSE_PREFIX):]
    return action_type if action_type in ACTION_CATALOG else None


def _inline_refs(schema: dict) -> dict:
    """Replace each `$ref` with its `$defs` entry and drop `$defs`. Some
    providers do not resolve references, and their models then send a nested
    object such as `changes` as a JSON string, which validation rejects."""
    defs = schema.get("$defs") or {}

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return walk(defs[ref.split("/")[-1]])
            return {key: walk(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [walk(value) for value in node]
        return node

    return walk(schema)


def propose_tool_definitions(allowed_types: set[str] | frozenset[str]) -> list[dict]:
    """One propose tool per catalog type the user may run (AC-11)."""
    tools = []
    for action_type, spec in ACTION_CATALOG.items():
        if action_type not in allowed_types:
            continue
        schema = _inline_refs(spec.params_model.model_json_schema())
        schema.pop("title", None)
        properties = dict(schema.get("properties") or {})
        for prop in properties.values():
            if isinstance(prop, dict):
                prop.pop("title", None)
        properties["summary"] = {
            "type": "string",
            "description": "One short sentence for the user saying what this will do and why.",
        }
        schema["properties"] = properties
        tools.append({
            "type": "function",
            "function": {"name": spec.tool_name, "description": spec.description, "parameters": schema},
        })
    return tools


# --------------------------------------------------------------------- executor


def _thread_runner(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="chat-action", daemon=True).start()


def _sync_scenario_to_jira(scenario_id: UUID, created: bool) -> None:
    """The Jira sync the scenario update route queues after a review. Runs in
    the action's worker thread, which has no event loop of its own."""
    import asyncio

    from backend.services.jira_sync_service import sync_scenario_created, sync_scenario_updated

    asyncio.run((sync_scenario_created if created else sync_scenario_updated)(scenario_id))


class ChatActionExecutor:
    """Builds proposals, rechecks them at confirm, and runs confirmed work."""

    def __init__(
        self,
        project_repository=None,
        workflow_service=None,
        audit: Callable[..., None] | None = None,
        runner: Callable[[Callable[[], None]], None] | None = None,
        project_service=None,
        script_jobs=None,
        execution_service=None,
        batch_events: dict | None = None,
        execution_events: dict | None = None,
        scenario_sync: Callable[[UUID, bool], None] | None = None,
        bug_report_service=None,
    ):
        self._repo = project_repository
        # A service or a zero argument callable returning one, so the app can
        # hand over its module global and tests can swap it.
        self._workflow_service = workflow_service
        self._audit = audit
        self._runner = runner or _thread_runner
        self._project_service = project_service
        self._script_jobs = script_jobs
        self._execution_service = execution_service
        # The in memory event logs of single runs (keyed by test case ID) and
        # batches (keyed by batch ID). Tests pass plain dicts.
        self._batch_events = batch_events
        self._execution_events = execution_events
        # Jira sync after a scenario review, as the Scenarios screen does.
        # Tests pass a stub so no Jira call is made.
        self._scenario_sync = scenario_sync or _sync_scenario_to_jira
        # BugReportService, or a zero argument callable returning one. Tests pass a fake.
        self._bug_report_service = bug_report_service

    @property
    def repo(self):
        if self._repo is None:
            from backend.repository.project_repository import get_project_repository

            return get_project_repository()
        return self._repo

    @property
    def workflow(self):
        service = self._workflow_service
        if callable(service) and not hasattr(service, "generate_scenarios"):
            service = service()
        if service is None:
            from backend.services.workflow_service import WorkflowService

            self._workflow_service = service = WorkflowService()
        return service

    @staticmethod
    def _resolve_dependency(value, default_factory):
        dependency = value
        if callable(dependency) and not hasattr(dependency, "start"):
            dependency = dependency()
        return dependency if dependency is not None else default_factory()

    @property
    def project_service(self):
        return self._resolve_dependency(
            self._project_service,
            lambda: __import__("backend.services.project_service", fromlist=["ProjectService"]).ProjectService(),
        )

    @property
    def script_jobs(self):
        return self._resolve_dependency(
            self._script_jobs,
            lambda: __import__("backend.main", fromlist=["script_generation_jobs"]).script_generation_jobs,
        )

    @property
    def execution_service(self):
        return self._resolve_dependency(
            self._execution_service,
            lambda: __import__("backend.services.execution_service", fromlist=["ExecutionService"]).ExecutionService(),
        )

    @property
    def batch_events(self) -> dict:
        if self._batch_events is not None:
            return self._batch_events
        from backend.services.execution_service import active_batch_events

        return active_batch_events

    @property
    def execution_events(self) -> dict:
        if self._execution_events is not None:
            return self._execution_events
        from backend.services.workflow_service import active_execution_events

        return active_execution_events

    def audit(self, event: str, *, user_id, project_id, action: dict) -> None:
        """AC-22: one entry per confirm, cancel, and final result."""
        if self._audit is None:
            return
        try:
            self._audit(event, user_id=user_id, project_id=project_id, action=action)
        except Exception:
            logger.exception("Chat action audit failed for %s", action.get("action_id"))

    # ----------------------------------------------------------------- propose

    def propose(self, project, action_type: str, arguments: dict | None) -> dict:
        """A full `proposed` action from the model's arguments. Raises
        ActionInvalid with a message the model can read and correct."""
        spec = ACTION_CATALOG.get(action_type)
        if spec is None:
            raise ActionInvalid(f"unknown action {action_type}")
        if not isinstance(arguments, dict):
            raise ActionInvalid("arguments were not valid JSON")
        arguments = dict(arguments)
        summary = arguments.pop("summary", "")
        for name in spec.server_fields:
            arguments.pop(name, None)
        params = self._validate(spec, arguments)
        resolved = self._resolve(spec, project, params)
        model_summary = " ".join(str(summary or "").split())[:SUMMARY_MAX]
        return {
            "action_id": str(uuid4()),
            "type": spec.type,
            "params": resolved["params"],
            "summary": model_summary,
            "targets": resolved["targets"],
            "danger": resolved["danger"],
            "permission": {"module": spec.module, "action": spec.permission_action},
            "affects": list(spec.affects),
            "status": "proposed",
            "job_ref": {"kind": spec.job_kind, "id": None},
            "result": None,
            "error": None,
            "confirmed_by": None,
            "updated_at": utc_now().isoformat(),
        }

    @staticmethod
    def _validate(spec: ActionSpec, arguments: dict) -> BaseModel:
        try:
            return spec.params_model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in error['loc']) or 'arguments'}: {error['msg']}" for error in exc.errors()
            )
            raise ActionInvalid(f"invalid arguments: {problems}") from None

    def _resolve(self, spec: ActionSpec, project, params: BaseModel) -> dict:
        resolver = getattr(self, f"_resolve_{spec.type}")
        return resolver(project, params)

    # ------------------------------------------------------------ project scope

    def _live_requirement_ids(self, project) -> set[str]:
        """The project's requirements that are not deleted. The project's own
        `requirements` list and the JSON `get_requirement` both keep deleted
        rows, so neither is enough on its own."""
        return {str(r.id) for r in self.repo.get_requirements(project.id)}

    def _requirement(self, project, requirement_id: UUID):
        if str(requirement_id) not in self._live_requirement_ids(project):
            raise ActionInvalid("requirement not found in this project")
        requirement = self.repo.get_requirement(requirement_id)
        if requirement is None:
            raise ActionInvalid("requirement not found in this project")
        return requirement

    def _live_scenarios(self, requirement_id: UUID) -> list:
        return [s for s in self.repo.get_scenarios(requirement_id) if not getattr(s, "is_deleted", False)]

    def _scenario(self, project, scenario_id: UUID):
        scenario = self.repo.get_scenario(scenario_id)
        if (
            scenario is None
            or getattr(scenario, "is_deleted", False)
            or str(scenario.requirement_id) not in self._live_requirement_ids(project)
        ):
            raise ActionInvalid("scenario not found in this project")
        return scenario

    def _test_case(self, project, test_case_id: UUID):
        test_case = self.repo.get_test_case(test_case_id)
        if test_case is None:
            raise ActionInvalid("test case not found in this project")
        scenario = self._scenario(project, test_case.scenario_id)
        return test_case, scenario

    def _execution(self, project, execution_id: UUID):
        """An execution of this project. Its test case may since have been
        deleted, so the test case is not required."""
        execution = self.repo.get_execution_result(project.id, execution_id)
        if execution is None or getattr(execution, "is_deleted", False):
            raise ActionInvalid("execution not found in this project")
        return execution

    def _execution_target(self, execution) -> dict:
        """An execution has no title: its name is its test case's title, else
        the first 8 characters of its ID (spec 0005, value sourcing)."""
        case = self.repo.get_test_case(execution.test_case_id, include_deleted=True)
        name = getattr(case, "title", None) or str(execution.id)[:8]
        return {
            "type": "execution",
            "id": str(execution.id),
            "name": f"{name} ({_status_value(execution.status)})",
        }

    @staticmethod
    def _target(item_type: str, item) -> dict:
        name = (
            getattr(item, "title", None)
            or getattr(item, "scenario_name", None)
            or "Untitled"
        )
        return {"type": item_type, "id": str(item.id), "name": name}

    @staticmethod
    def _changes(params: BaseModel) -> dict:
        return params.changes.model_dump(mode="json", exclude_unset=True)

    @staticmethod
    def _require_changes(changes: dict) -> None:
        if not changes:
            raise ActionInvalid("at least one field must be changed")

    # ------------------------------------------------------- generate_scenarios

    def _resolve_generate_scenarios(self, project, params: GenerateScenariosParams) -> dict:
        requirement = self._requirement(project, params.requirement_id)
        targets = [{"type": "requirement", "id": str(requirement.id), "name": requirement.title}]
        stored = params.model_dump(mode="json")
        danger = "change"
        if params.mode == "replace":
            replaced = self._live_scenarios(requirement.id)
            stored["replace_ids"] = [str(s.id) for s in replaced]
            if replaced:
                danger = "delete"
                targets += [{"type": "scenario", "id": str(s.id), "name": s.scenario_name} for s in replaced]
        return {"params": stored, "targets": targets, "danger": danger}

    def _recheck_generate_scenarios(self, project, action: dict) -> None:
        params = action.get("params") or {}
        model = self._validate(ACTION_CATALOG["generate_scenarios"], {k: v for k, v in params.items() if k != "replace_ids"})
        self._requirement(project, model.requirement_id)
        live = {str(s.id) for s in self._live_scenarios(model.requirement_id)}
        if any(scenario_id not in live for scenario_id in params.get("replace_ids") or []):
            raise ActionInvalid("a scenario to replace no longer exists")

    def _run_generate_scenarios(self, project, action: dict, user_id) -> dict:
        params = action["params"]
        requirement_id = UUID(params["requirement_id"])
        # Generate first; the old scenarios go only once the new ones exist,
        # so a failed generation leaves them in place.
        created = self.workflow.generate_scenarios(project.id, requirement_id, int(params.get("count", 3)), mode="append")
        replaced = 0
        for scenario_id in params.get("replace_ids") or []:
            test_cases = self.repo.get_test_cases(UUID(scenario_id))
            if self.repo.delete_scenario(UUID(scenario_id), deleted_by=user_id):
                replaced += 1
                queue_test_case_removals(project.id, test_cases)
        summary = f"Created {len(created)} scenario{'s' if len(created) != 1 else ''}"
        if replaced:
            summary += f", replaced {replaced}"
        return {"summary": summary, "link_view": "scenarios", "link_id": str(requirement_id)}

    def _resolve_generate_test_cases(self, project, params: GenerateTestCasesParams) -> dict:
        requirement = self._requirement(project, params.requirement_id)
        return {"params": params.model_dump(mode="json"), "targets": [self._target("requirement", requirement)], "danger": "change"}

    def _run_generate_test_cases(self, project, action: dict, user_id) -> dict:
        params = action["params"]
        created = self.workflow.generate_test_cases(project.id, UUID(params["requirement_id"]))
        return {
            "summary": f"Created {len(created)} test case{'s' if len(created) != 1 else ''}",
            "link_view": "testcases",
            "link_id": params["requirement_id"],
        }

    def _resolve_generate_script(self, project, params: GenerateScriptParams) -> dict:
        case, _ = self._test_case(project, params.test_case_id)
        return {"params": params.model_dump(mode="json"), "targets": [self._target("test_case", case)], "danger": "change"}

    def _run_generate_script(self, project, action: dict, user_id) -> dict:
        params = action["params"]
        job = self.script_jobs.start(project.id, UUID(params["test_case_id"]), params.get("framework") or "Playwright")
        return {"job_ref": {"kind": "script_job", "id": str(job.id)}}

    def _resolve_run_test_case(self, project, params: RunTestCaseParams) -> dict:
        case, _ = self._test_case(project, params.test_case_id)
        return {"params": params.model_dump(mode="json"), "targets": [self._target("test_case", case)], "danger": "change"}

    def _run_run_test_case(self, project, action: dict, user_id) -> dict:
        execution_id = self.execution_service.start_test_case_run(
            project.id,
            UUID(action["params"]["test_case_id"]),
            workflow_service=self.workflow,
        )
        return {"job_ref": {"kind": "execution", "id": str(execution_id)}}

    def _resolve_run_batch(self, project, params: RunBatchParams) -> dict:
        cases = [self._test_case(project, case_id)[0] for case_id in params.test_case_ids]
        if len({case.id for case in cases}) != len(cases):
            raise ActionInvalid("test_case_ids must be unique")
        if any(not case.playwright_script for case in cases):
            raise ActionInvalid("every selected test case must have a script")
        return {
            "params": params.model_dump(mode="json"),
            "targets": [self._target("test_case", case) for case in cases],
            "danger": "change",
        }

    def _run_run_batch(self, project, action: dict, user_id) -> dict:
        batch_id = self.execution_service.start_batch_run(
            project.id, [UUID(value) for value in action["params"]["test_case_ids"]]
        )
        return {"job_ref": {"kind": "batch", "id": str(batch_id)}}

    def _resolve_create_requirement(self, project, params: CreateRequirementParams) -> dict:
        return {"params": params.model_dump(mode="json"), "targets": [], "danger": "change"}

    def _run_create_requirement(self, project, action: dict, user_id) -> dict:
        params = action["params"]
        # Same call as the create requirement route: add_requirement picks a
        # requirement key unused in the project, so nothing is overwritten.
        requirement = self.project_service.add_requirement(
            project_id=project.id,
            title=params["title"],
            description=params["description"],
            priority=params["priority"],
            business_domain=params["business_domain"],
            attachments=[],
            requirement_title=params["title"],
        )
        return {
            "summary": f"Created requirement '{requirement.title}'",
            "link_view": "requirements",
            "link_id": str(requirement.id),
        }

    def _resolve_update_requirement(self, project, params: UpdateRequirementParams) -> dict:
        requirement = self._requirement(project, params.requirement_id)
        changes = self._changes(params)
        self._require_changes(changes)
        old_values = {key: getattr(requirement, key) for key in changes}
        stored = params.model_dump(mode="json")
        stored["changes"] = changes
        stored["old_values"] = {key: str(value) if isinstance(value, UUID) else value for key, value in old_values.items()}
        return {
            "params": stored,
            "targets": [self._target("requirement", requirement)],
            "danger": "change",
        }

    def _run_update_requirement(self, project, action: dict, user_id) -> dict:
        params = action["params"]
        requirement = self._requirement(project, UUID(params["requirement_id"]))
        changes = params["changes"]
        updated = requirement.model_copy(update=changes)
        # The JSON store keeps its stored priority and business domain unless
        # they are passed in, so pass them or those edits are lost.
        self.repo.save_requirement(updated, updated.priority, updated.business_domain)
        return {
            "summary": f"Updated {len(changes)} fields on '{updated.title}'",
            "link_view": "requirements",
            "link_id": str(updated.id),
        }

    def _resolve_delete_requirement(self, project, params: DeleteRequirementParams) -> dict:
        requirement = self._requirement(project, params.requirement_id)
        scenarios = self._live_scenarios(requirement.id)
        test_cases = [case for scenario in scenarios for case in self.repo.get_test_cases(scenario.id)]
        test_case_count = len(test_cases)
        params_json = params.model_dump(mode="json")
        params_json["scenario_ids"] = [str(scenario.id) for scenario in scenarios]
        params_json["test_case_ids"] = [str(case.id) for case in test_cases]
        params_json["jira_issue_key"] = requirement.jira_issue_key
        return {
            "params": params_json,
            "targets": [self._target("requirement", requirement), {
                "type": "children", "id": str(requirement.id),
                "name": f"{len(scenarios)} scenarios and {test_case_count} test cases",
            }],
            "danger": "delete",
        }

    def _run_delete_requirement(self, project, action: dict, user_id) -> dict:
        requirement_id = UUID(action["params"]["requirement_id"])
        params = action["params"]
        # Bug keys of its test cases, read before the delete (spec 0006, AC-14a).
        defect_keys = [
            getattr(self.repo.get_test_case(UUID(str(test_case_id))), "jira_issue_key", None)
            for test_case_id in params.get("test_case_ids") or []
        ]
        if not self.project_service.delete_requirement(requirement_id, deleted_by=user_id):
            raise ActionInvalid(CHANGED_MESSAGE)
        # Knowledge (spec 0006, AC-15): the story and the removed test cases leave the index.
        queue_knowledge_source(project.id, "jira_story", params.get("jira_issue_key"), remove=True)
        for test_case_id in params.get("test_case_ids") or []:
            queue_knowledge_source(project.id, "test_case", test_case_id, remove=True)
        queue_defect_keys(project.id, defect_keys)
        return {"summary": "Deleted requirement and its scenarios and test cases", "link_view": "requirements", "link_id": None}

    def _resolve_update_scenario(self, project, params: UpdateScenarioParams) -> dict:
        scenario = self._scenario(project, params.scenario_id)
        changes = self._changes(params)
        self._require_changes(changes)
        validated = ScenarioUpdate.model_validate(changes).model_dump(mode="json", exclude_unset=True)
        old_values = {key: getattr(scenario, key) for key in validated}
        stored = params.model_dump(mode="json")
        stored["changes"] = validated
        stored["old_values"] = old_values
        return {"params": stored, "targets": [self._target("scenario", scenario)], "danger": "change"}

    def _run_update_scenario(self, project, action: dict, user_id) -> dict:
        params = action["params"]
        scenario_id = UUID(params["scenario_id"])
        updated = self.project_service.update_scenario(scenario_id, **params["changes"])
        if updated is None:
            raise ActionInvalid(CHANGED_MESSAGE)
        return {"summary": f"Updated {len(params['changes'])} fields on '{updated.scenario_name}'", "link_view": "scenarios", "link_id": str(updated.requirement_id)}

    def _resolve_review_scenario(self, project, params: ReviewScenarioParams) -> dict:
        scenario = self._scenario(project, params.scenario_id)
        return {"params": params.model_dump(mode="json"), "targets": [self._target("scenario", scenario)], "danger": "change"}

    def _run_review_scenario(self, project, action: dict, user_id) -> dict:
        params = action["params"]
        scenario = self._scenario(project, UUID(params["scenario_id"]))
        had_jira_issue = bool(getattr(scenario, "jira_issue_key", None))
        approved = params["decision"] == "approved"
        # Same call as the approve and reject buttons on the Scenarios screen.
        # Rejecting removes the scenario's test cases, so read them first.
        removed = [] if approved else self.repo.get_test_cases(scenario.id)
        updated = self.project_service.update_scenario(
            scenario_id=scenario.id, **({"approved": True} if approved else {"rejected": True}),
        )
        if updated is None:
            raise ActionInvalid(CHANGED_MESSAGE)
        if removed:
            queue_test_case_removals(project.id, removed)
        if approved or getattr(updated, "jira_issue_key", None):
            try:
                self._scenario_sync(updated.id, approved and not had_jira_issue)
            except Exception:
                # The review is saved; a Jira failure is logged, not reported as a failed action.
                logger.exception("Jira sync failed after chat review of scenario %s", updated.id)
        verb = "Approved" if approved else "Rejected"
        return {"summary": f"{verb} '{updated.scenario_name}'", "link_view": "scenarios", "link_id": str(updated.requirement_id)}

    def _resolve_duplicate_scenario(self, project, params: DeleteScenarioParams) -> dict:
        scenario = self._scenario(project, params.scenario_id)
        return {"params": params.model_dump(mode="json"), "targets": [self._target("scenario", scenario)], "danger": "change"}

    def _run_duplicate_scenario(self, project, action: dict, user_id) -> dict:
        scenario = self.project_service.duplicate_scenario(UUID(action["params"]["scenario_id"]))
        if scenario is None:
            raise ActionInvalid(CHANGED_MESSAGE)
        return {"summary": f"Created scenario '{scenario.scenario_name}'", "link_view": "scenarios", "link_id": str(scenario.requirement_id)}

    def _resolve_delete_scenario(self, project, params: DeleteScenarioParams) -> dict:
        scenario = self._scenario(project, params.scenario_id)
        return {"params": params.model_dump(mode="json"), "targets": [self._target("scenario", scenario)], "danger": "delete"}

    def _run_delete_scenario(self, project, action: dict, user_id) -> dict:
        scenario_id = UUID(action["params"]["scenario_id"])
        test_cases = self.repo.get_test_cases(scenario_id)
        if not self.repo.delete_scenario(scenario_id, deleted_by=user_id):
            raise ActionInvalid(CHANGED_MESSAGE)
        queue_test_case_removals(project.id, test_cases)
        return {"summary": "Deleted scenario", "link_view": "scenarios", "link_id": None}

    def _resolve_update_test_case(self, project, params: UpdateTestCaseParams) -> dict:
        case, _ = self._test_case(project, params.test_case_id)
        changes = self._changes(params)
        self._require_changes(changes)
        validated = TestCaseUpdate.model_validate({key: value for key, value in changes.items() if key != "data"}).model_dump(mode="json", exclude_unset=True)
        if "data" in changes:
            validated["data"] = changes["data"]
        old_values = {("data" if key == "data" else key): getattr(case, "test_data" if key == "data" else key) for key in validated}
        stored = params.model_dump(mode="json")
        stored["changes"] = validated
        stored["old_values"] = old_values
        return {"params": stored, "targets": [self._target("test_case", case)], "danger": "change"}

    def _run_update_test_case(self, project, action: dict, user_id) -> dict:
        params = action["params"]
        case, _ = self._test_case(project, UUID(params["test_case_id"]))
        changes = dict(params["changes"])
        data = changes.pop("data", None)
        if data is not None:
            changes["test_data"] = data
        updated = self.repo.update_test_case(case.model_copy(update=changes))
        if updated is None:
            raise ActionInvalid(CHANGED_MESSAGE)
        queue_knowledge_source(project.id, "test_case", updated.id)
        return {"summary": f"Updated {len(params['changes'])} fields on '{updated.title}'", "link_view": "testcases", "link_id": str(updated.scenario_id)}

    def _resolve_review_test_case(self, project, params: ReviewTestCaseParams) -> dict:
        case, _ = self._test_case(project, params.test_case_id)
        return {"params": params.model_dump(mode="json"), "targets": [self._target("test_case", case)], "danger": "change"}

    def _run_review_test_case(self, project, action: dict, user_id) -> dict:
        params = action["params"]
        case, _ = self._test_case(project, UUID(params["test_case_id"]))
        # Same call as the approve and reject buttons on the Test Cases screen.
        updated = self.project_service.update_test_case(
            test_case_id=case.id, evaluation_status=EvaluationStatus(params["decision"]),
        )
        if updated is None:
            raise ActionInvalid(CHANGED_MESSAGE)
        queue_knowledge_source(project.id, "test_case", updated.id)
        verb = "Approved" if params["decision"] == "approved" else "Rejected"
        return {"summary": f"{verb} '{updated.title}'", "link_view": "testcases", "link_id": str(updated.scenario_id)}

    def _resolve_generate_test_data(self, project, params: GenerateTestDataParams) -> dict:
        case, _ = self._test_case(project, params.test_case_id)
        # The same checks the service makes, so the user hears it now, not after Run.
        if (getattr(project, "framework", "") or "").lower() != "playwright":
            raise ActionInvalid("test data generation needs a Playwright project")
        if (case.automation_framework or "").lower() != "playwright":
            raise ActionInvalid("test data generation needs a Playwright test case")
        return {"params": params.model_dump(mode="json"), "targets": [self._target("test_case", case)], "danger": "change"}

    def _run_generate_test_data(self, project, action: dict, user_id) -> dict:
        case, scenario = self._test_case(project, UUID(action["params"]["test_case_id"]))
        _, updated = self.workflow.generate_test_data_for_test_case(project.id, scenario.requirement_id, case.id)
        count = len(updated.test_data_sets or [])
        return {
            "summary": f"Generated {count} test data set{'s' if count != 1 else ''} for '{updated.title}'",
            "link_view": "testcases",
            "link_id": str(updated.scenario_id),
        }

    def _resolve_delete_test_case(self, project, params: DeleteTestCaseParams) -> dict:
        case, _ = self._test_case(project, params.test_case_id)
        return {"params": params.model_dump(mode="json"), "targets": [self._target("test_case", case)], "danger": "delete"}

    def _run_delete_test_case(self, project, action: dict, user_id) -> dict:
        case_id = UUID(action["params"]["test_case_id"])
        case, _ = self._test_case(project, case_id)
        if not self.repo.delete_test_case(case_id, deleted_by=user_id):
            raise ActionInvalid(CHANGED_MESSAGE)
        return {"summary": "Deleted test case", "link_view": "testcases", "link_id": str(case.scenario_id)}

    def _resolve_delete_execution(self, project, params: DeleteExecutionParams) -> dict:
        execution = self._execution(project, params.execution_id)
        return {
            "params": params.model_dump(mode="json"),
            "targets": [self._execution_target(execution)],
            "danger": "delete",
        }

    def _run_delete_execution(self, project, action: dict, user_id) -> dict:
        execution_id = UUID(action["params"]["execution_id"])
        if not self.repo.delete_execution(execution_id, deleted_by=user_id):
            raise ActionInvalid(CHANGED_MESSAGE)
        return {"summary": "Deleted execution", "link_view": "executions", "link_id": None}

    def _resolve_delete_failed_executions(self, project, params: DeleteFailedExecutionsParams) -> dict:
        # "Failed" matches the UI's delete failed executions button: no verdict
        # (error) counts as failed too.
        failed = [
            result for result in self.repo.get_execution_results(project.id)
            if _status_value(result.status) in FAILED_EXECUTION_STATUSES and not getattr(result, "is_deleted", False)
        ][:MAX_FAILED_EXECUTIONS]
        if not failed:
            raise ActionInvalid("there are no failed executions in this project")
        targets = [self._execution_target(result) for result in failed[:FAILED_TARGETS_SHOWN]]
        if len(failed) > FAILED_TARGETS_SHOWN:
            targets.append({
                "type": "children", "id": "remaining",
                "name": f"and {len(failed) - FAILED_TARGETS_SHOWN} more failed executions",
            })
        return {
            "params": {"execution_ids": [str(result.id) for result in failed]},
            "targets": targets,
            "danger": "delete",
        }

    def _recheck_delete_failed_executions(self, project, action: dict) -> None:
        """Only the executions listed at propose time are deleted, so each of
        them must still exist. Ones that failed since are left alone."""
        execution_ids = (action.get("params") or {}).get("execution_ids") or []
        if not execution_ids:
            raise ActionInvalid("no executions to delete")
        for value in execution_ids:
            self._execution(project, UUID(value))

    def _run_delete_failed_executions(self, project, action: dict, user_id) -> dict:
        deleted = sum(
            1 for value in action["params"].get("execution_ids") or []
            if self.repo.delete_execution(UUID(value), deleted_by=user_id)
        )
        return {"summary": f"Deleted {deleted} execution{'s' if deleted != 1 else ''}", "link_view": "executions", "link_id": None}

    def _recheck_delete_requirement(self, project, action: dict) -> None:
        requirement_id = UUID(action["params"]["requirement_id"])
        self._requirement(project, requirement_id)
        current_ids = sorted(str(s.id) for s in self._live_scenarios(requirement_id))
        proposed_ids = sorted(action["params"].get("scenario_ids") or [])
        current_test_case_ids = sorted(
            str(case.id)
            for scenario_id in [UUID(value) for value in current_ids]
            for case in self.repo.get_test_cases(scenario_id)
        )
        proposed_test_case_ids = sorted(action["params"].get("test_case_ids") or [])
        if current_ids != proposed_ids or current_test_case_ids != proposed_test_case_ids:
            raise ActionInvalid(CHANGED_MESSAGE)

    # ------------------------------------------------------------ bug reports

    @property
    def bug_reports(self):
        def default():
            from backend.services.bug_report_service import BugReportService

            return BugReportService(project_repo=self.repo)

        return self._resolve_dependency(self._bug_report_service, default)

    def _bug_report(self, project, report_id: UUID):
        report = self.bug_reports.get(project.id, report_id)
        if report is None:
            raise ActionInvalid("bug report not found in this project")
        return report

    def _resolve_report_bug(self, project, params: ReportBugParams) -> dict:
        execution = self._execution(project, params.execution_id)
        if _status_value(execution.status) not in FAILED_EXECUTION_STATUSES:
            raise ActionInvalid("only a failed or errored execution can be reported as a bug")
        return {
            "params": params.model_dump(mode="json"),
            "targets": [self._execution_target(execution)],
            "danger": "change",
        }

    def _run_report_bug(self, project, action: dict, user_id) -> dict:
        from backend.services.bug_report_service import BugReportError

        execution_id = UUID(action["params"]["execution_id"])
        try:
            report = self.bug_reports.report_for_execution(project.id, execution_id, actor=str(user_id))
        except (LookupError, BugReportError) as exc:
            raise ActionInvalid(str(exc)) from None
        return {
            "summary": f"Drafted bug report '{report.title}'",
            "link_view": "bugs",
            "link_id": str(report.id),
        }

    def _resolve_submit_bug_report(self, project, params: SubmitBugReportParams) -> dict:
        from backend.models.bug_report import EDITABLE_STATUSES

        report = self._bug_report(project, params.bug_report_id)
        if report.status not in EDITABLE_STATUSES:
            raise ActionInvalid(f"this bug report is already {report.status.value}")
        return {
            "params": params.model_dump(mode="json"),
            "targets": [{"type": "bug_report", "id": str(report.id), "name": report.title}],
            "danger": "change",
        }

    def _run_submit_bug_report(self, project, action: dict, user_id) -> dict:
        from backend.models.bug_report import BugReportStatus
        from backend.services.bug_report_service import BugReportError

        report_id = UUID(action["params"]["bug_report_id"])
        try:
            report = self.bug_reports.submit(project.id, report_id, actor=str(user_id))
        except (LookupError, BugReportError) as exc:
            raise ActionInvalid(str(exc)) from None
        if report.status == BugReportStatus.SUBMIT_FAILED:
            raise RuntimeError(f"Jira submit failed: {report.submit_error or 'unknown error'}")
        if report.status == BugReportStatus.LINKED_DUPLICATE:
            summary = f"Linked to existing Jira bug {report.duplicate_of_jira_key}"
        else:
            summary = f"Filed in Jira as {report.jira_key}"
        return {"summary": summary, "link_view": "bugs", "link_id": str(report.id)}

    # ------------------------------------------------------------------ confirm

    def recheck(self, project, action: dict) -> None:
        """Confirm rule 4: every target still exists in the project and the saved
        params still pass the params model. Raises ActionInvalid."""
        checker = getattr(self, f"_recheck_{action.get('type')}", None)
        try:
            if checker is not None:
                checker(project, action)
                return
            action_type = action.get("type")
            spec = ACTION_CATALOG.get(action_type)
            if spec is None:
                raise ActionInvalid(CHANGED_MESSAGE)
            params = dict(action.get("params") or {})
            for name in spec.server_fields:
                params.pop(name, None)
            model = self._validate(spec, params)
            getattr(self, f"_resolve_{action_type}")(project, model)
        except ActionInvalid as exc:
            logger.info("Chat action %s failed its confirm recheck: %s", action.get("action_id"), exc)
            raise ActionInvalid(CHANGED_MESSAGE) from None

    def start_status(self, action: dict) -> str:
        """Confirm moves a proposal to `running` for inline work, else `queued`."""
        return "running" if (action.get("job_ref") or {}).get("kind") == "inline" else "queued"

    def start(self, chats, project, message_id: UUID, action: dict, user_id) -> None:
        """Run the confirmed work. Inline actions run in a worker thread that
        writes the final status itself."""
        runner = getattr(self, f"_run_{action['type']}")
        job_kind = (action.get("job_ref") or {}).get("kind")

        def work() -> None:
            try:
                result = runner(project, action, user_id)
                if job_kind != "inline":
                    job_ref = result.get("job_ref") if isinstance(result, dict) else None
                    if not job_ref or not job_ref.get("id"):
                        raise RuntimeError("The background job did not return an ID")
                    chats.compare_and_set_action_status(
                        message_id, {"queued", "running"}, "queued", {"job_ref": job_ref},
                    )
                    return
                final, patch = "succeeded", {"result": result, "error": None}
            except Exception as exc:
                logger.exception("Chat action %s failed", action.get("action_id"))
                final, patch = "failed", {"error": _error_text(exc)}
            self.finish(chats, message_id, action, final, patch, project_id=project.id)

        self._runner(work)

    def finish(self, chats, message_id: UUID, action: dict, final: str, patch: dict, *, project_id) -> dict:
        """Move an active action to a terminal status. The writer that wins the
        move also writes the final audit entry. If the message is gone (its
        thread was deleted while the work ran), the audit entry is still
        written (AC-20)."""
        expected = {action.get("status")} if action.get("status") in ACTIVE else set(ACTIVE)
        updated = chats.compare_and_set_action_status(message_id, expected, final, patch)
        if updated is not None:
            self.audit(final, user_id=updated.get("confirmed_by"), project_id=project_id, action=updated)
            return updated
        current = chats.get_message(message_id)
        if current is None or not current.action:
            logger.warning("Chat action %s finished but its message %s is gone", action.get("action_id"), message_id)
            gone = {**action, **patch, "status": final}
            self.audit(final, user_id=action.get("confirmed_by"), project_id=project_id, action=gone)
            return gone
        return current.action

    # --------------------------------------------------------------- refresh

    def job_status(self, action: dict, *, project_id) -> tuple[str, dict | None, str | None] | None:
        """(status, result, error) of a job backed action, read from its job
        (spec 0005, result and status mapping). None while the worker has not
        recorded the job ID yet, or for inline work, whose worker writes its
        own status. A job that is gone maps to `failed` with LOST_ERROR."""
        job_ref = action.get("job_ref") or {}
        kind, job_id = job_ref.get("kind"), job_ref.get("id")
        if not job_id:
            return None
        if kind == "script_job":
            return self._script_job_status(project_id, UUID(job_id))
        if kind == "execution":
            return self._execution_status(project_id, UUID(job_id), (action.get("params") or {}).get("test_case_id"))
        if kind == "batch":
            return self._batch_status(str(job_id))
        return None

    def _script_job_status(self, project_id, job_id: UUID):
        job = self.script_jobs.get(project_id, job_id)
        if job is None:
            return "failed", None, LOST_ERROR
        if job.status == "running":
            return "running", None, None
        if job.status == "completed":
            return "succeeded", {"summary": "Script generated", "link_view": "playwright", "link_id": str(job.test_case_id)}, None
        return "failed", None, (job.error or "Script generation failed")[:300]

    def _execution_status(self, project_id, execution_id: UUID, test_case_id):
        execution = self.repo.get_execution_result(project_id, execution_id)
        if execution is not None:
            verdict = _status_value(execution.status)
            if verdict == "error":
                return "failed", None, (execution.error_message or "The run ended without a verdict")[:300]
            if verdict == "passed":
                summary = f"Passed in {execution.duration_seconds:.0f} s"
            elif verdict == "skipped":
                summary = "Skipped"
            else:
                summary = f"Failed: {(execution.error_message or 'no error message')[:120]}"
            return "succeeded", {"summary": summary, "link_view": "executions", "link_id": str(execution.id)}, None
        # No record yet. The run's event log is keyed by test case; the starter
        # resets it before the run is queued, so an old run's log never counts.
        events = self.execution_events.get(str(test_case_id)) if test_case_id else None
        if events is None:
            return "failed", None, LOST_ERROR
        if not events:
            return "queued", None, None
        last = events[-1]
        if last.get("status") == "Completed":
            # The run finished without saving a result: it raised.
            return "failed", None, str(last.get("log") or "The run failed before saving a result")[:300]
        return "running", None, None

    def _batch_status(self, batch_id: str):
        events = self.batch_events.get(batch_id)
        if events is None:
            return "failed", None, LOST_ERROR
        last = events[-1] if events else None
        if last is None:
            return "queued", None, None
        if last.get("status") == "Error":
            return "failed", None, str(last.get("log") or "Batch execution failed")[:300]
        if last.get("status") == "Completed":
            summary = (
                f"{last.get('passed_count', 0)} passed, {last.get('failed_count', 0)} failed, "
                f"{last.get('skipped_count', 0)} skipped"
            )
            return "succeeded", {"summary": summary, "link_view": "executions", "link_id": batch_id}, None
        return "running", None, None

    def refresh(self, chats, message_id: UUID, action: dict, *, project_id) -> dict:
        """AC-15: the action as the status card should show it, pulled from its
        job and saved. Inline actions are written by their worker, so their
        stored status is already current."""
        if action.get("status") not in ACTIVE:
            return action
        found = self.job_status(action, project_id=project_id)
        if found is None:
            return action
        status, result, error = found
        if status in ACTIVE:
            if status == action.get("status"):
                return action
            moved = chats.compare_and_set_action_status(message_id, {action.get("status")}, status)
            if moved is not None:
                return moved
            current = chats.get_message(message_id)
            return current.action if current and current.action else action
        return self.finish(chats, message_id, action, status, {"result": result, "error": error}, project_id=project_id)

    def recover(self, chats, message_id: UUID, action: dict, *, project_id) -> dict:
        """Startup recovery. Inline workers and in memory jobs died with the
        process, so anything not already finished becomes failed with
        RESTART_ERROR. A run that saved its result before the restart still
        finishes from that result."""
        found = None
        if (action.get("job_ref") or {}).get("kind") != "inline":
            found = self.job_status(action, project_id=project_id)
        if found is not None and found[0] not in ACTIVE and found[2] != LOST_ERROR:
            status, result, error = found
            return self.finish(chats, message_id, action, status, {"result": result, "error": error}, project_id=project_id)
        return self.finish(chats, message_id, action, "failed", {"error": RESTART_ERROR}, project_id=project_id)


def _status_value(status) -> str:
    return str(getattr(status, "value", status))


def _error_text(exc: Exception) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text[:300]


def action_audit_fields(event: str, action: dict) -> dict:
    """The `field_changes` payload of a chat action audit entry."""
    return {
        "source": "chat",
        "event": event,
        "action_id": action.get("action_id"),
        "action_type": action.get("type"),
        "target_ids": [t.get("id") for t in action.get("targets") or []],
        "outcome": action.get("status"),
        "error": action.get("error"),
    }


PROPOSED_RESULT = "proposed, awaiting user confirmation"
ONE_ACTION_RESULT = "error: only one action per turn"
