"""Chat assistant actions, slice 4 (spec 0005): the rest of the catalog.

Covers the typed DELETE confirm, edits that keep every changed field, failed
execution cleanup, and status refresh by `job_ref` for scripts, single runs,
and batches, including jobs that are gone. Every dependency is a fake and the
work runs in the calling thread, so no provider, database, or browser is
needed (AC-26)."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from backend.config.settings import ChatConfig
from backend.repository.chat_repository import JsonChatRepository
from backend.services.chat_actions import (
    LOST_ERROR,
    RESTART_ERROR,
    ActionInvalid,
    ChatActionExecutor,
)
from backend.services.chat_service import ChatInvalid, ChatService


def _status(value):
    return SimpleNamespace(value=value)


class FakeRepo:
    """One project, one requirement, one scenario, and its test cases."""

    def __init__(self, project, requirement, scenario, cases):
        self.project = project
        self.requirement = requirement
        self.scenario = scenario
        self.cases = {c.id: c for c in cases}
        self.executions: dict = {}
        self.saved_requirements: list = []
        self.deleted: list = []

    def get_requirements(self, project_id):
        live = project_id == self.project.id and not getattr(self.requirement, "is_deleted", False)
        return [self.requirement] if live else []

    def get_requirement(self, requirement_id):
        return self.requirement if requirement_id == self.requirement.id else None

    def save_requirement(self, requirement, priority=None, business_domain=None, attachments=None):
        self.saved_requirements.append((requirement, priority, business_domain))

    def get_scenarios(self, requirement_id):
        return [self.scenario] if requirement_id == self.requirement.id else []

    def get_scenario(self, scenario_id):
        return self.scenario if scenario_id == self.scenario.id else None

    def get_test_cases(self, scenario_id):
        return [c for c in self.cases.values() if c.scenario_id == scenario_id and not c.is_deleted]

    def get_test_case(self, test_case_id, include_deleted=False):
        case = self.cases.get(test_case_id)
        if case is None or (case.is_deleted and not include_deleted):
            return None
        return case

    def delete_test_case(self, test_case_id, deleted_by=None):
        case = self.cases.get(test_case_id)
        if case is None or case.is_deleted:
            return False
        case.is_deleted = True
        self.deleted.append(("test_case", test_case_id, deleted_by))
        return True

    def get_execution_results(self, project_id):
        return [e for e in self.executions.values() if not e.is_deleted]

    def get_execution_result(self, project_id, execution_id):
        return self.executions.get(execution_id) if project_id == self.project.id else None

    def delete_execution(self, execution_id, deleted_by=None):
        execution = self.executions.get(execution_id)
        if execution is None or execution.is_deleted:
            return False
        execution.is_deleted = True
        self.deleted.append(("execution", execution_id, deleted_by))
        return True

    def add_execution(self, test_case_id, status, **fields):
        execution = SimpleNamespace(
            id=uuid4(), test_case_id=test_case_id, status=_status(status), is_deleted=False,
            duration_seconds=fields.get("duration_seconds", 0.0), error_message=fields.get("error_message"),
        )
        self.executions[execution.id] = execution
        return execution


class FakeProjectService:
    def __init__(self):
        self.added: list[dict] = []

    def add_requirement(self, **fields):
        self.added.append(fields)
        return SimpleNamespace(id=uuid4(), title=fields["title"])

    def update_test_case(self, test_case_id, **fields):
        self.added.append({"test_case_id": test_case_id, **fields})
        return SimpleNamespace(id=test_case_id, title="Case", scenario_id=uuid4())

    def update_scenario(self, scenario_id, **fields):
        self.added.append({"scenario_id": scenario_id, **fields})
        return SimpleNamespace(
            id=scenario_id, scenario_name="Valid sign in", requirement_id=uuid4(), jira_issue_key=None,
        )


class FakeScriptJobs:
    def __init__(self):
        self.jobs: dict = {}

    def start(self, project_id, test_case_id, framework):
        job = SimpleNamespace(id=uuid4(), project_id=project_id, test_case_id=test_case_id, status="running", error="")
        self.jobs[job.id] = job
        return job

    def get(self, project_id, job_id):
        job = self.jobs.get(job_id)
        return job if job is not None and job.project_id == project_id else None


class FakeExecutionService:
    def __init__(self, execution_events, batch_events):
        self.execution_events = execution_events
        self.batch_events = batch_events
        self.runs: list = []

    def start_test_case_run(self, project_id, test_case_id, workflow_service=None):
        execution_id = uuid4()
        self.execution_events[str(test_case_id)] = []
        self.runs.append(("case", test_case_id, execution_id))
        return execution_id

    def start_batch_run(self, project_id, test_case_ids):
        batch_id = uuid4()
        self.batch_events[str(batch_id)] = []
        self.runs.append(("batch", test_case_ids, batch_id))
        return batch_id


@pytest.fixture


def world(tmp_path):
    requirement = SimpleNamespace(
        id=uuid4(), title="Login works", description="Users sign in", priority="medium", business_domain="general",
    )
    requirement.model_copy = lambda update: SimpleNamespace(**{**vars(requirement), **update})
    project = SimpleNamespace(id=uuid4(), name="Shop", requirements=[requirement.id])
    scenario = SimpleNamespace(
        id=uuid4(), requirement_id=requirement.id, scenario_name="Valid sign in", is_deleted=False,
    )
    cases = [
        SimpleNamespace(
            id=uuid4(), scenario_id=scenario.id, title=f"Case {i}", playwright_script="script", is_deleted=False,
        )
        for i in range(2)
    ]
    repo = FakeRepo(project, requirement, scenario, cases)
    execution_events: dict = {}
    batch_events: dict = {}
    audits: list = []
    jira_syncs: list = []
    executor = ChatActionExecutor(
        project_repository=repo,
        workflow_service=SimpleNamespace(generate_scenarios=None),
        audit=lambda event, **fields: audits.append((event, fields)),
        runner=lambda work: work(),
        project_service=FakeProjectService(),
        script_jobs=FakeScriptJobs(),
        execution_service=FakeExecutionService(execution_events, batch_events),
        batch_events=batch_events,
        execution_events=execution_events,
        scenario_sync=lambda scenario_id, created: jira_syncs.append((scenario_id, created)),
    )
    chats = JsonChatRepository(tmp_path / "chat_store.json")
    service = ChatService(
        llm_service=None, chat_repository=chats, project_repository=repo,
        settings=SimpleNamespace(chat=ChatConfig()), toolbox=None, action_executor=executor,
        system_prompt="sys", guide="",
    )
    user_id = uuid4()
    thread = chats.create_thread(project.id, user_id, "t")

    def card(action_type, arguments):
        """A saved `proposed` card, as a propose tool call would leave it."""
        action = executor.propose(project, action_type, arguments)
        message = chats.append_message(thread.id, "assistant", "")
        chats.set_message_action(message.id, action)
        return message.id

    def confirm(message_id, confirm_text=None):
        return service.confirm_action(project, user_id, message_id, confirm_text, can=lambda module, action: True)

    return SimpleNamespace(
        project=project, requirement=requirement, scenario=scenario, cases=cases, repo=repo, executor=executor,
        chats=chats, service=service, user_id=user_id, card=card, confirm=confirm, audits=audits,
        execution_events=execution_events, batch_events=batch_events, jira_syncs=jira_syncs,
    )


# ------------------------------------------------------------------ deletes


def test_a_delete_runs_only_with_delete_typed(world):
    case = world.cases[0]
    message_id = world.card("delete_test_case", {"test_case_id": str(case.id)})

    for typed in (None, "", "delete", "DELETE "):
        with pytest.raises(ChatInvalid):
            world.confirm(message_id, typed)
    assert not case.is_deleted, "a rejected confirm changes nothing"
    assert world.chats.get_message(message_id).action["status"] == "proposed"

    action = world.confirm(message_id, "DELETE")
    assert action["status"] == "succeeded"
    assert case.is_deleted
    assert world.repo.deleted == [("test_case", case.id, world.user_id)]
    assert [event for event, _ in world.audits] == ["confirmed", "succeeded"]


def test_delete_cards_list_every_record(world):
    message_id = world.card("delete_test_case", {"test_case_id": str(world.cases[1].id)})
    action = world.chats.get_message(message_id).action
    assert action["danger"] == "delete"
    assert action["targets"] == [{"type": "test_case", "id": str(world.cases[1].id), "name": "Case 1"}]


def test_failed_execution_cleanup_lists_failed_and_errored_runs(world):
    gone_case = world.cases[1]
    failed = world.repo.add_execution(world.cases[0].id, "failed")
    errored = world.repo.add_execution(gone_case.id, "error")
    world.repo.add_execution(world.cases[0].id, "passed")
    gone_case.is_deleted = True

    message_id = world.card("delete_failed_executions", {"execution_ids": [str(uuid4())]})
    action = world.chats.get_message(message_id).action

    assert sorted(action["params"]["execution_ids"]) == sorted([str(failed.id), str(errored.id)]), \
        "the server fills the IDs; the model's list is ignored"
    names = {t["id"]: t["name"] for t in action["targets"]}
    assert names[str(failed.id)] == "Case 0 (failed)"
    assert names[str(errored.id)] == "Case 1 (error)", "a deleted test case's title still names its run"

    later = world.repo.add_execution(world.cases[0].id, "failed")
    action = world.confirm(message_id, "DELETE")
    assert action["result"]["summary"] == "Deleted 2 executions"
    assert not later.is_deleted, "runs that failed after the proposal are left alone"


def test_an_execution_with_no_test_case_is_named_by_its_id(world):
    orphan = world.repo.add_execution(uuid4(), "failed")
    message_id = world.card("delete_execution", {"execution_id": str(orphan.id)})
    (target,) = world.chats.get_message(message_id).action["targets"]
    assert target["name"] == f"{str(orphan.id)[:8]} (failed)"


def test_failed_execution_cleanup_needs_failed_runs(world):
    world.repo.add_execution(world.cases[0].id, "passed")
    with pytest.raises(ActionInvalid, match="no failed executions"):
        world.executor.propose(world.project, "delete_failed_executions", {})


def test_failed_execution_cleanup_rechecks_its_saved_list(world):
    failed = world.repo.add_execution(world.cases[0].id, "failed")
    message_id = world.card("delete_failed_executions", {})
    failed.is_deleted = True
    with pytest.raises(ChatInvalid):
        world.confirm(message_id, "DELETE")


# -------------------------------------------------------------------- edits


def test_a_requirement_edit_keeps_priority_and_business_domain(world):
    message_id = world.card("update_requirement", {
        "requirement_id": str(world.requirement.id),
        "changes": {"priority": "high", "business_domain": "payments"},
    })
    action = world.chats.get_message(message_id).action
    assert action["params"]["old_values"] == {"priority": "medium", "business_domain": "general"}

    assert world.confirm(message_id)["status"] == "succeeded"
    ((saved, priority, business_domain),) = world.repo.saved_requirements
    assert (saved.priority, priority, business_domain) == ("high", "high", "payments")


def test_a_deleted_requirement_cannot_be_edited(world):
    message_id = world.card("update_requirement", {
        "requirement_id": str(world.requirement.id), "changes": {"title": "New"},
    })
    world.requirement.is_deleted = True
    with pytest.raises(ChatInvalid):
        world.confirm(message_id)
    assert world.repo.saved_requirements == []


def test_create_requirement_goes_through_the_route_service(world):
    message_id = world.card("create_requirement", {"title": "Checkout", "description": "Pay by card"})
    action = world.confirm(message_id)
    assert action["status"] == "succeeded"
    (added,) = world.executor.project_service.added
    assert added["project_id"] == world.project.id
    assert (added["title"], added["requirement_title"]) == ("Checkout", "Checkout")


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_a_test_case_is_approved_or_rejected_through_the_button_service(world, decision):
    case = world.cases[0]
    message_id = world.card("review_test_case", {"test_case_id": str(case.id), "decision": decision})
    action = world.confirm(message_id)
    assert action["status"] == "succeeded"
    (call,) = world.executor.project_service.added
    assert call["test_case_id"] == case.id
    assert call["evaluation_status"].value == decision
    assert action["result"]["summary"].startswith("Approved" if decision == "approved" else "Rejected")


def test_a_scenario_is_approved_through_the_button_service_and_synced_to_jira(world):
    message_id = world.card("review_scenario", {"scenario_id": str(world.scenario.id), "decision": "approved"})
    action = world.confirm(message_id)
    assert action["status"] == "succeeded"
    (call,) = world.executor.project_service.added
    assert call == {"scenario_id": world.scenario.id, "approved": True}
    assert world.jira_syncs == [(world.scenario.id, True)]
    assert action["result"]["summary"] == "Approved 'Valid sign in'"
    assert action["result"]["link_view"] == "scenarios"


def test_a_rejected_scenario_without_a_jira_issue_is_not_synced(world):
    action = world.confirm(world.card("review_scenario", {"scenario_id": str(world.scenario.id), "decision": "rejected"}))
    assert action["status"] == "succeeded"
    (call,) = world.executor.project_service.added
    assert call == {"scenario_id": world.scenario.id, "rejected": True}
    assert world.jira_syncs == []
    assert action["result"]["summary"].startswith("Rejected")


def test_a_jira_failure_does_not_fail_the_scenario_approval(world):
    def failing_sync(scenario_id, created):
        raise RuntimeError("jira down")

    world.executor._scenario_sync = failing_sync
    action = world.confirm(world.card("review_scenario", {"scenario_id": str(world.scenario.id), "decision": "approved"}))
    assert action["status"] == "succeeded"


def test_scenario_review_rejects_unknown_scenarios_and_decisions(world):
    with pytest.raises(ActionInvalid, match="scenario not found"):
        world.executor.propose(world.project, "review_scenario", {"scenario_id": str(uuid4()), "decision": "approved"})
    with pytest.raises(ActionInvalid, match="decision"):
        world.executor.propose(world.project, "review_scenario", {
            "scenario_id": str(world.scenario.id), "decision": "pending",
        })


def test_review_only_accepts_approve_or_reject(world):
    with pytest.raises(ActionInvalid, match="decision"):
        world.executor.propose(world.project, "review_test_case", {
            "test_case_id": str(world.cases[0].id), "decision": "pending",
        })


def test_test_data_is_generated_for_the_test_case_and_its_requirement(world):
    world.project.framework = "Playwright"
    case = world.cases[0]
    case.automation_framework = "playwright"
    calls = []

    def generate(project_id, requirement_id, test_case_id):
        calls.append((project_id, requirement_id, test_case_id))
        return {}, SimpleNamespace(title=case.title, scenario_id=case.scenario_id, test_data_sets=[{}] * 20)

    world.executor._workflow_service.generate_test_data_for_test_case = generate
    action = world.confirm(world.card("generate_test_data", {"test_case_id": str(case.id)}))
    assert action["status"] == "succeeded"
    assert calls == [(world.project.id, world.requirement.id, case.id)]
    assert action["result"]["summary"] == f"Generated 20 test data sets for '{case.title}'"


def test_test_data_needs_a_playwright_project_and_test_case(world):
    case = world.cases[0]
    world.project.framework = "Selenium"
    case.automation_framework = "playwright"
    with pytest.raises(ActionInvalid, match="Playwright project"):
        world.executor.propose(world.project, "generate_test_data", {"test_case_id": str(case.id)})
    world.project.framework = "Playwright"
    case.automation_framework = "manual"
    with pytest.raises(ActionInvalid, match="Playwright test case"):
        world.executor.propose(world.project, "generate_test_data", {"test_case_id": str(case.id)})


# ------------------------------------------------------------ job status


def _confirmed(world, action_type, arguments):
    message_id = world.card(action_type, arguments)
    action = world.confirm(message_id)
    assert action["status"] == "queued"
    assert action["job_ref"]["id"], "the worker records the job ID"
    return message_id, action


def test_a_single_run_moves_from_queued_to_its_verdict(world):
    case = world.cases[0]
    message_id, action = _confirmed(world, "run_test_case", {"test_case_id": str(case.id)})

    assert world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)["status"] == "queued"
    world.execution_events[str(case.id)].append({"status": "Running"})
    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert action["status"] == "running"

    execution_id = UUID(action["job_ref"]["id"])
    execution = world.repo.add_execution(case.id, "passed", duration_seconds=41.6)
    execution.id = execution_id
    world.repo.executions[execution_id] = execution
    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert action["status"] == "succeeded"
    assert action["result"] == {"summary": "Passed in 42 s", "link_view": "executions", "link_id": str(execution_id)}


def test_a_failing_test_is_a_run_that_succeeded(world):
    case = world.cases[0]
    message_id, action = _confirmed(world, "run_test_case", {"test_case_id": str(case.id)})

    execution = world.repo.add_execution(case.id, "failed", error_message="Button not found")
    world.repo.executions[UUID(action["job_ref"]["id"])] = execution
    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert action["status"] == "succeeded"
    assert action["result"]["summary"] == "Failed: Button not found"


def test_a_run_that_raised_before_saving_fails(world):
    case = world.cases[0]
    message_id, action = _confirmed(world, "run_test_case", {"test_case_id": str(case.id)})
    world.execution_events[str(case.id)].append({"status": "Completed", "log": "Scenario not found"})
    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert (action["status"], action["error"]) == ("failed", "Scenario not found")


def test_a_run_whose_log_is_gone_fails_as_lost(world):
    case = world.cases[0]
    message_id, action = _confirmed(world, "run_test_case", {"test_case_id": str(case.id)})
    world.execution_events.clear()
    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert (action["status"], action["error"]) == ("failed", LOST_ERROR)


def test_a_batch_reports_its_counts(world):
    ids = [str(c.id) for c in world.cases]
    message_id, action = _confirmed(world, "run_batch", {"test_case_ids": ids})
    events = world.batch_events[action["job_ref"]["id"]]

    events.append({"status": "Running"})
    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert action["status"] == "running"

    events.append({"status": "Completed", "passed_count": 1, "failed_count": 1, "skipped_count": 0})
    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert action["status"] == "succeeded"
    assert action["result"]["summary"] == "1 passed, 1 failed, 0 skipped"


def test_a_batch_that_is_gone_fails_as_lost(world):
    ids = [str(world.cases[0].id)]
    message_id, action = _confirmed(world, "run_batch", {"test_case_ids": ids})
    world.batch_events.clear()
    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert (action["status"], action["error"]) == ("failed", LOST_ERROR)


def test_script_generation_follows_its_job(world):

    case = world.cases[0]
    message_id, action = _confirmed(world, "generate_script", {"test_case_id": str(case.id)})
    job = world.executor.script_jobs.jobs[UUID(action["job_ref"]["id"])]

    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert action["status"] == "running"
    job.status = "completed"
    action = world.executor.refresh(world.chats, message_id, action, project_id=world.project.id)
    assert action["status"] == "succeeded"
    assert action["result"]["link_view"] == "playwright"
    assert [event for event, _ in world.audits][-1] == "succeeded", "the final status writes one audit entry"


def test_restart_recovery_fails_job_backed_work_unless_its_result_was_saved(world):

    script_id, script_action = _confirmed(world, "generate_script", {"test_case_id": str(world.cases[0].id)})
    run_id, run_action = _confirmed(world, "run_test_case", {"test_case_id": str(world.cases[1].id)})
    execution = world.repo.add_execution(world.cases[1].id, "passed", duration_seconds=3)
    world.repo.executions[UUID(run_action["job_ref"]["id"])] = execution

    # The restart empties every in memory job and event log.
    world.executor.script_jobs.jobs.clear()
    world.execution_events.clear()
    world.service.recover_after_restart()

    script = world.chats.get_message(script_id).action
    assert (script["status"], script["error"]) == ("failed", RESTART_ERROR)
    assert world.chats.get_message(run_id).action["status"] == "succeeded"
