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
    AccessibilityAgent (accessibility report per run)
        ↓
    BugReportingAgent (drafts or reuses a bug report per Product Bug failure,
                       and per serious accessibility violation)
        ↓
    ReportAgent

Inputs:
    - state.project_id
    - state.execution_results: triaged results
    - state.accessibility_results: one accessibility report per run (spec 0011)
    - state.generated_test_cases: test case title, steps and expected result
    - state.retrieved_context: project knowledge from the analysis retrieval stage

Owned outputs:
    - state.bug_report_ids (functional and accessibility bugs)
    - accessibility finding.bug_report_id, saved through BugReportService
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
        failed = [r for r in state.execution_results if getattr(r.status, "value", r.status) in _FAILED]
        a11y_reports = list(state.accessibility_results)
        if not failed and not a11y_reports:
            state.add_log(f"{self.name}: no failed results or accessibility reports, nothing to report.")
            return state
        needs_project = any(r.failure_category == "Product Bug" for r in failed) or a11y_reports
        if state.project_id is None and needs_project:
            raise ValueError(f"{self.name} requires state.project_id to draft bug reports")

        state.add_log(
            f"{self.name} started for {len(failed)} failed result(s) and "
            f"{len(a11y_reports)} accessibility report(s)"
        )
        test_cases = {tc.id: tc for tc in state.generated_test_cases}
        drafted = self._draft_functional(state, failed, test_cases)
        a11y_drafted = self._draft_accessibility(state, a11y_reports, test_cases)
        state.add_log(f"{self.name} completed: {drafted} bug report(s), {a11y_drafted} accessibility bug(s)")
        return state

    def _draft_functional(self, state: WorkflowState, failed, test_cases) -> int:
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
            self._remember(state, report.id)
            drafted += 1
            occurrences = len(report.occurrences)
            verb = "reused" if occurrences > 1 else "drafted"
            state.add_log(
                f"{self.name}: {verb} bug report {report.id} ({report.status.value}, "
                f"{occurrences} occurrence(s)) for {result.id}"
            )
        return drafted

    def _draft_accessibility(self, state: WorkflowState, a11y_reports, test_cases) -> int:
        """Spec 0011, AC-6: violations at or above the project's threshold become
        `Accessibility Issue` drafts, one open bug per finding signature."""
        results = {r.id: r for r in state.execution_results}
        drafted = 0
        for a11y_report in a11y_reports:
            result = results.get(a11y_report.execution_id)
            bugs = self.bug_report_service.draft_from_accessibility(
                state.project_id,
                a11y_report,
                result=result,
                test_case=test_cases.get(a11y_report.test_case_id),
            )
            for report in bugs:
                self._remember(state, report.id)
            drafted += len(bugs)
            if bugs:
                state.add_log(
                    f"{self.name}: {len(bugs)} accessibility bug(s) for report {a11y_report.id}"
                )
        return drafted

    @staticmethod
    def _remember(state: WorkflowState, report_id) -> None:
        if report_id not in state.bug_report_ids:
            state.bug_report_ids.append(report_id)
