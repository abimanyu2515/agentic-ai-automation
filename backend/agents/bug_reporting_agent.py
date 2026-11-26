"""
BugReportingAgent

Purpose:
    Turns every triaged "Product Bug" failure in the run into a structured bug
    report draft that a person reviews before it is filed in Jira.

Workflow Position:
    ExecutionAgent
        ↓
    ExecutionAnalysisAgent (sets failure_category, root cause, evidence)
        ↓
    BugReportingAgent (drafts or reuses a bug report per Product Bug failure)
        ↓
    ReportAgent

Inputs:
    - state.project_id
    - state.execution_results: triaged results
    - state.generated_test_cases: test case title, steps and expected result
    - state.retrieved_context: project knowledge from the analysis retrieval stage

Owned outputs:
    - state.bug_report_ids
    - execution_result.bug_report_id
    - execution_result.jira_bug_id / jira_bug_url, only when the project
      auto-submits or the failure matches a report already filed in Jira

All drafting, dedupe, redaction and Jira rules live in BugReportService.
"""

from backend.agents.base import BaseAgent
from backend.models.execution_result import ExecutionStatus
from backend.models.state import WorkflowState
from backend.services.bug_report_service import BugReportService

_FAILED = (ExecutionStatus.FAILED.value, ExecutionStatus.ERROR.value)


class BugReportingAgent(BaseAgent):
    name = "Bug Reporting Agent"

    def __init__(self, bug_report_service: BugReportService | None = None, llm_service=None):
        self.bug_report_service = bug_report_service or BugReportService(llm_service=llm_service)

    @property
    def llm_service(self):
        return self.bug_report_service.llm_service

    def run(self, state: WorkflowState) -> WorkflowState:
        # Spec 0011, AC-11: only the latest result per test case, so a test case
        # that passed on a heal rerun gets no bug report.
        failed = [r for r in state.latest_results() if getattr(r.status, "value", r.status) in _FAILED]
        if not failed:
            state.add_log(f"{self.name}: no failed results, nothing to report.")
            return state
        if state.project_id is None and any(r.failure_category == "Product Bug" for r in failed):
            raise ValueError(f"{self.name} requires state.project_id to draft bug reports")

        state.add_log(f"{self.name} started for {len(failed)} failed result(s)")
        test_cases = {tc.id: tc for tc in state.generated_test_cases}
        drafted = 0
        for result in failed:
            if result.failure_category != "Product Bug":
                state.add_log(f"{self.name}: skipped {result.id}, category is {result.failure_category}")
                continue
            report = self.bug_report_service.draft_from_execution(
                state.project_id,
                result,
                test_case=test_cases.get(result.test_case_id),
                retrieved_context=state.retrieved_context,
            )
            if report is None:
                state.add_log(f"{self.name}: could not draft a bug report for {result.id}")
                continue
            if report.id not in state.bug_report_ids:
                state.bug_report_ids.append(report.id)
            drafted += 1
            occurrences = len(report.occurrences)
            verb = "reused" if occurrences > 1 else "drafted"
            state.add_log(
                f"{self.name}: {verb} bug report {report.id} ({report.status.value}, "
                f"{occurrences} occurrence(s)) for {result.id}"
            )

        state.add_log(f"{self.name} completed: {drafted} bug report(s)")
        return state
