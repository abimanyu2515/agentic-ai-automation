"""Chat assistant and bug reports: the report_bug and submit_bug_report actions
and the list_bug_reports / get_bug_report read tools. Every dependency is a fake."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.models.bug_report import BugReport, BugReportStatus
from backend.repository.bug_report_repository import InMemoryBugReportRepository
from backend.services.bug_report_service import BugReportError
from backend.services.chat_actions import ActionInvalid
from backend.services.chat_tools import READ_TOOL_NAMES, ChatToolbox
from backend.tests.test_chat_action_catalog import world  # noqa: F401  (fixture)


class FakeBugReports:
    def __init__(self, project_id):
        self.project_id = project_id
        self.reports: dict = {}
        self.calls: list = []
        self.submit_status = BugReportStatus.SUBMITTED

    def add(self, **fields):
        report = BugReport(
            project_id=self.project_id, execution_id=uuid4(), test_case_id=uuid4(), title="Refund skips PIN", **fields,
        )
        self.reports[report.id] = report
        return report

    def get(self, project_id, report_id):
        report = self.reports.get(report_id)
        return report if report and report.project_id == project_id else None

    def report_for_execution(self, project_id, execution_id, actor):
        self.calls.append(("report", execution_id, actor))
        return self.add()

    def submit(self, project_id, report_id, actor):
        self.calls.append(("submit", report_id, actor))
        report = self.reports[report_id]
        if report.status not in (BugReportStatus.DRAFT, BugReportStatus.SUBMIT_FAILED):
            raise BugReportError("already submitted")
        report.status = self.submit_status
        if self.submit_status == BugReportStatus.SUBMIT_FAILED:
            report.submit_error = "Jira is down"
        elif self.submit_status == BugReportStatus.LINKED_DUPLICATE:
            report.duplicate_of_jira_key = "SHOP-3"
        else:
            report.jira_key = "SHOP-7"
        return report


@pytest.fixture
def bugs(world):  # noqa: F811
    fake = FakeBugReports(world.project.id)
    world.executor._bug_report_service = fake
    return fake


# ---------------------------------------------------------------- report_bug


def test_report_bug_drafts_only_after_confirm(world, bugs):  # noqa: F811
    failed = world.repo.add_execution(world.cases[0].id, "failed")
    message_id = world.card("report_bug", {"execution_id": str(failed.id)})

    action = world.chats.get_message(message_id).action
    assert action["targets"] == [{"type": "execution", "id": str(failed.id), "name": "Case 0 (failed)"}]
    assert action["permission"] == {"module": "Executions", "action": "edit"}
    assert bugs.calls == [], "proposing does nothing"

    done = world.confirm(message_id)
    assert done["status"] == "succeeded"
    assert bugs.calls == [("report", failed.id, str(world.user_id))]
    assert done["result"]["link_view"] == "bugs" and done["result"]["link_id"]


def test_report_bug_rejects_passed_and_unknown_runs(world, bugs):  # noqa: F811
    passed = world.repo.add_execution(world.cases[0].id, "passed")
    with pytest.raises(ActionInvalid, match="only a failed"):
        world.executor.propose(world.project, "report_bug", {"execution_id": str(passed.id)})
    with pytest.raises(ActionInvalid, match="execution not found"):
        world.executor.propose(world.project, "report_bug", {"execution_id": str(uuid4())})


# ---------------------------------------------------------------- submit_bug_report


@pytest.mark.parametrize("status, summary", [
    (BugReportStatus.SUBMITTED, "Filed in Jira as SHOP-7"),
    (BugReportStatus.LINKED_DUPLICATE, "Linked to existing Jira bug SHOP-3"),
])
def test_submit_bug_report_files_in_jira(world, bugs, status, summary):  # noqa: F811
    bugs.submit_status = status
    report = bugs.add()
    message_id = world.card("submit_bug_report", {"bug_report_id": str(report.id)})
    assert world.chats.get_message(message_id).action["targets"][0]["name"] == "Refund skips PIN"

    done = world.confirm(message_id)

    assert done["status"] == "succeeded" and done["result"]["summary"] == summary


def test_submit_bug_report_failure_marks_the_card_failed(world, bugs):  # noqa: F811
    bugs.submit_status = BugReportStatus.SUBMIT_FAILED
    report = bugs.add()
    done = world.confirm(world.card("submit_bug_report", {"bug_report_id": str(report.id)}))
    assert done["status"] == "failed" and "Jira is down" in done["error"]


def test_submit_bug_report_needs_an_open_report_in_this_project(world, bugs):  # noqa: F811
    submitted = bugs.add(status=BugReportStatus.SUBMITTED)
    with pytest.raises(ActionInvalid, match="already submitted"):
        world.executor.propose(world.project, "submit_bug_report", {"bug_report_id": str(submitted.id)})
    with pytest.raises(ActionInvalid, match="not found"):
        world.executor.propose(world.project, "submit_bug_report", {"bug_report_id": str(uuid4())})


# ---------------------------------------------------------------- read tools


def _toolbox(store):
    repo = SimpleNamespace(get_test_cases_for_project=lambda project_id: [])
    return ChatToolbox(project_repository=repo, bug_report_repository=store)


def test_bug_report_tools_are_project_scoped():
    project = SimpleNamespace(id=uuid4())
    store = InMemoryBugReportRepository()
    mine = BugReport(project_id=project.id, execution_id=uuid4(), test_case_id=uuid4(), title="Mine", evidence=["e1"])
    other = BugReport(project_id=uuid4(), execution_id=uuid4(), test_case_id=uuid4(), title="Other")
    store.save(mine)
    store.save(other)
    toolbox = _toolbox(store)

    assert {"list_bug_reports", "get_bug_report"} <= READ_TOOL_NAMES
    listed = json.loads(toolbox.run(project, "list_bug_reports", {}))
    assert [row["title"] for row in listed["items"]] == ["Mine"]
    assert json.loads(toolbox.run(project, "list_bug_reports", {"status": "submitted"}))["items"] == []
    assert "unknown status" in json.loads(toolbox.run(project, "list_bug_reports", {"status": "nope"}))["error"]

    detail = json.loads(toolbox.run(project, "get_bug_report", {"bug_report_id": str(mine.id)}))
    assert detail["title"] == "Mine" and detail["evidence"] == ["e1"]
    assert "error" in json.loads(toolbox.run(project, "get_bug_report", {"bug_report_id": str(other.id)}))
