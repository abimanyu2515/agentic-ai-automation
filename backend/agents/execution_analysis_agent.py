"""
ExecutionAnalysisAgent (the log analyser)

Purpose:
    Triages failed test executions. Each failure is categorized as a Product Bug,
    a Test Environment Issue, or a Flaky Test / Automation Issue, so only real
    defects flow on to BugReportingAgent.

Workflow Position:
    ExecutionAgent
        ↓
    (retrieval_analysis, when RAG workflow retrieval is on)
        ↓
    ExecutionAnalysisAgent
        ↓
    BugReportingAgent (only processes Product Bugs)
        ↓
    ReportAgent

Inputs:
    - state.execution_results: results produced by ExecutionAgent
    - state.generated_test_cases: test case context (title, steps, expected result)
    - state.project_id: used for run history and credential redaction
    - state.retrieved_context: optional project knowledge

Outputs (on each non-passed ExecutionResult):
    - failure_category, root_cause_summary, trace_analysis, screenshot_findings
    - suggest_retry, retest_pending_candidate
    - failing_step, evidence, confidence, flaky_score, frontend_error_detected,
      frontend_error_details, run_history_summary

The analysis itself lives in LogAnalysisService, which the batch and webhook
execution paths also use. It reads the run's report.json, error-context.md and
trace.zip (text only), plus earlier runs of the same test case. It falls back to
"Test Environment Issue" with suggest_retry=True when the LLM call fails.
"""

from backend.agents.base import BaseAgent
from backend.models.execution_result import ExecutionStatus
from backend.models.state import WorkflowState
from backend.services.llm import LLMService
from backend.services.log_analysis_service import LogAnalysisService


class ExecutionAnalysisAgent(BaseAgent):
    """Log analyser: triages every failed execution result in the state."""

    name = "Execution Analysis Agent"

    def __init__(
        self,
        llm_service: LLMService | None = None,
        analysis_service: LogAnalysisService | None = None,
    ):
        """
        Args:
            llm_service: LLM service instance (injected for testing)
            analysis_service: LogAnalysisService instance (injected for testing)
        """
        self.llm_service = llm_service or LLMService(role="analysis", agent="execution_analysis_agent")
        self.analysis_service = analysis_service or LogAnalysisService(llm_service=self.llm_service)

    def run(self, state: WorkflowState) -> WorkflowState:
        if not state.execution_results:
            state.add_log(f"{self.name}: no execution results found to analyze.")
            return state

        failed = [r for r in state.execution_results if r.status != ExecutionStatus.PASSED]
        if not failed:
            for result in state.execution_results:
                self.analysis_service.analyze(state.project_id, result)
            state.add_log(f"{self.name}: skipped analysis as execution passed.")
            return state

        state.add_log(f"{self.name} started analysis of {len(failed)} failed execution(s)")
        cases = {tc.id: tc for tc in state.generated_test_cases}
        for result in state.execution_results:
            self.analysis_service.analyze(
                state.project_id,
                result,
                cases.get(result.test_case_id),
                state.retrieved_context,
            )
            if result.status == ExecutionStatus.PASSED:
                continue
            state.add_log(
                f"{self.name} triaged execution {result.id}: Category={result.failure_category}, "
                f"Confidence={result.confidence}, Flaky score={result.flaky_score}, "
                f"Root Cause={result.root_cause_summary}"
            )
        state.add_log(f"{self.name} completed analysis")
        return state
