"""
AccessibilityAgent

Purpose:
    Turns the axe-core scan of every executed run into a stored accessibility
    report with findings, counts, and a release gate result (spec 0011).

Workflow Position:
    ExecutionAgent (the runner scans the pages each test visits)
        ↓
    ExecutionAnalysisAgent
        ↓
    AccessibilityAgent (one report per execution result)
        ↓
    BugReportingAgent
        ↓
    ReportAgent

Inputs:
    - state.project_id
    - state.execution_results, each with its project_config_snapshot
    - the run folder's a11y.json, written by the runner

Owned outputs:
    - state.accessibility_results

All normalizing, redaction, gate, and storage rules live in AccessibilityService.
A scan failure is recorded on the report and never changes a run's result, so
this agent never raises: a run without a project is logged and skipped.
"""

from backend.agents.base import BaseAgent
from backend.models.accessibility import AccessibilityReport
from backend.models.state import WorkflowState
from backend.services.accessibility_service import AccessibilityService, read_a11y_file


def _default_raw_reader(result):
    from backend.services.playwright_runner import ARTIFACTS_ROOT

    # The runner uses the test case id as its run folder.
    return read_a11y_file(ARTIFACTS_ROOT / str(result.test_case_id))


class AccessibilityAgent(BaseAgent):
    name = "Accessibility Agent"

    def __init__(self, accessibility_service: AccessibilityService | None = None, raw_reader=None):
        self.accessibility_service = accessibility_service or AccessibilityService()
        self.raw_reader = raw_reader or _default_raw_reader

    def run(self, state: WorkflowState) -> WorkflowState:
        if not state.execution_results:
            state.add_log(f"{self.name}: no execution results, nothing to scan.")
            return state
        if state.project_id is None:
            # Reports belong to a project; a run without one is never failed for it (AC-2).
            state.add_log(f"{self.name}: skipped, the run has no project to save reports to.")
            return state

        state.add_log(f"{self.name} started for {len(state.execution_results)} result(s)")
        done = {r.execution_id for r in state.accessibility_results}
        for result in state.execution_results:
            if result.id in done:
                continue
            try:
                raw = self.raw_reader(result)
            except Exception:
                raw = None
            report: AccessibilityReport = self.accessibility_service.process(state.project_id, result, raw)
            state.accessibility_results.append(report)
            reason = f", {report.gate_reason}" if report.gate_reason else ""
            state.add_log(
                f"{self.name}: {result.id} gate {report.gate_status.value}{reason}, "
                f"{len(report.findings)} finding(s) on {len(report.pages)} page(s)"
            )

        state.add_log(f"{self.name} completed")
        return state
