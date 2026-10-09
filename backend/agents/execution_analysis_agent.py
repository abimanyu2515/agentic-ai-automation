"""
ExecutionAnalysisAgent

Purpose:
    Triages test execution failures using LLM-powered root cause analysis.
    Categorizes failures as Product Bugs, Test Environment Issues, or Flaky Test/Automation Issues.
    Determines which failures warrant bug tickets versus retries.

Responsibilities:
    - Skip analysis for passed executions
    - Analyze failure logs and screenshots via LLM
    - Categorize failure into one of three categories
    - Extract root cause summary from error traces
    - Determine if test should be retried (flaky/environmental) or filed as bug
    - Populate execution result with triage metadata

Workflow Position:
    ExecutionAgent
        ↓
    ExecutionAnalysisAgent
        ↓
    DefectManagementAgent (only processes Product Bugs)
        ↓
    ReportAgent

Inputs:
    - state.execution_results: Latest execution result from ExecutionAgent
    - state.generated_test_cases: Test case context (steps, expected result)

Outputs:
    - execution_result.failure_category: "Product Bug" | "Test Environment Issue" | "Flaky Test / Automation Issue"
    - execution_result.root_cause_summary: Human-readable root cause explanation
    - execution_result.trace_analysis: Detailed trace log analysis
    - execution_result.screenshot_findings: Analysis of failure screenshot
    - execution_result.suggest_retry: Boolean indicating retry recommendation
    - execution_result.retest_pending_candidate: Boolean marking as bug candidate

Failure Categories:
    1. Product Bug:
        - Actual application defect
        - Incorrect business logic, validation failure, unexpected behavior
        - Warrants JIRA bug ticket creation
        - retest_pending_candidate = True
        
    2. Test Environment Issue:
        - Infrastructure problem (database down, network timeout, service unavailable)
        - Missing test data or configuration
        - suggest_retry = True
        
    3. Flaky Test / Automation Issue:
        - Selector instability, timing issues, race conditions
        - Automation script defect (incorrect locator, logic error)
        - suggest_retry = True

LLM Prompt:
    Inputs:
    - Test steps
    - Expected result
    - Error logs (trace analysis)
    - Screenshot description
    
    Output Schema: _ExecutionAnalysisResponse

Error Handling:
    If LLM analysis fails:
    - Default to "Test Environment Issue"
    - root_cause_summary includes LLM error message
    - suggest_retry = True
    - retest_pending_candidate = False

Design Philosophy:
    Automated triage reduces manual defect analysis workload.
    Only "Product Bug" category flows to DefectManagementAgent.
    Environment/flaky issues are logged but don't create JIRA tickets.
"""

from pydantic import BaseModel, Field
from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.models.execution_result import ExecutionStatus
from backend.services.llm import LLMService
from backend.utils.prompts import load_prompt


# ==========================================================
# LLM Output Schema
# ==========================================================

class _ExecutionAnalysisResponse(BaseModel):
    """Execution failure triage analysis from LLM."""
    failure_category: str = Field(description="One of: Product Bug, Test Environment Issue, Flaky Test / Automation Issue")
    root_cause_summary: str = Field(description="Summary of the root cause of this failure")
    trace_analysis: str = Field(description="Detailed analysis of trace log errors")
    screenshot_findings: str = Field(description="Findings from checking the screenshot")
    suggest_retry: bool = Field(description="True if test should be retried, False otherwise")
    retest_pending_candidate: bool = Field(description="True if it represents a bug candidate")


class ExecutionAnalysisAgent(BaseAgent):
    """
    Execution failure triage agent using LLM-powered root cause analysis.
    
    Categorizes failures to route Product Bugs to defect management
    while filtering out environmental and automation issues.
    """
    
    name = "Execution Analysis Agent"

    def __init__(self, llm_service: LLMService | None = None):
        """
        Initialize execution analysis agent.
        
        Args:
            llm_service: LLM service instance (injected for testing)
        """
        self.llm_service = llm_service or LLMService()

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Analyze test execution failure and determine root cause category.
        
        Args:
            state: Workflow state with execution_results and generated_test_cases
            
        Returns:
            Updated state with execution result triage metadata populated
            
        Process:
            1. Skip if no execution results exist
            2. Get latest execution result
            3. Skip analysis if execution passed
            4. Find corresponding test case for context
            5. Load execution analysis prompt with test details and error logs
            6. Call LLM for triage analysis
            7. Populate execution result with failure_category, root_cause, triage metadata
            8. On LLM failure, fall back to "Test Environment Issue" defaults
        """
        if not state.execution_results:
            state.add_log(f"{self.name}: no execution results found to analyze.")
            return state

        result = state.execution_results[-1]
        
        # If execution succeeded, skip failure analysis
        if result.status == ExecutionStatus.PASSED:
            result.failure_category = "None"
            result.root_cause_summary = "Execution passed successfully."
            result.trace_analysis = "N/A"
            result.screenshot_findings = "N/A"
            result.suggest_retry = False
            result.retest_pending_candidate = False
            state.add_log(f"{self.name}: skipped analysis as execution passed.")
            return state

        state.add_log(f"{self.name} started analysis for execution {result.id}")

        # Find the test case in state or construct dummy placeholder
        tc = next((t for t in state.generated_test_cases if t.id == result.test_case_id), None)
        tc_title = tc.title if tc else "Unknown Test Case"
        tc_steps = "\n".join(f"- {s}" for s in tc.steps) if tc else "N/A"
        tc_expected = tc.expected_result if tc else "N/A"

        failure_msg = result.error_message or "Unknown failure"
        screenshot_desc = "Screenshot captured during failure" if result.screenshot_path else "No screenshot captured"

        prompt = load_prompt(
            "execution_analysis_prompt.txt",
            steps=tc_steps,
            expected_result=tc_expected,
            logs=failure_msg,
            screenshot_desc=screenshot_desc
        )

        try:
            analysis = self.llm_service.structured_generate(
                user=prompt,
                response_model=_ExecutionAnalysisResponse
            )

            result.failure_category = analysis.failure_category
            result.root_cause_summary = analysis.root_cause_summary
            result.trace_analysis = analysis.trace_analysis
            result.screenshot_findings = analysis.screenshot_findings
            result.suggest_retry = analysis.suggest_retry
            result.retest_pending_candidate = analysis.retest_pending_candidate

            state.add_log(
                f"{self.name} triaged failure: Category={analysis.failure_category}, "
                f"Root Cause={analysis.root_cause_summary}"
            )
        except Exception as exc:
            state.add_log(f"{self.name} analysis failed: {exc}")
            # Populate fallback defaults
            result.failure_category = "Test Environment Issue"
            result.root_cause_summary = f"Analysis agent failed: {exc}. Execution error: {failure_msg}"
            result.trace_analysis = failure_msg
            result.screenshot_findings = "Unknown"
            result.suggest_retry = True
            result.retest_pending_candidate = False

        return state
