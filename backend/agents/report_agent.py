"""
ReportAgent

Purpose:
    Generates comprehensive execution summary reports by aggregating workflow state.
    Provides metrics, pass rates, and artifact references for stakeholder reporting.
    Uses AI to generate content-oriented, keyword-focused executive summaries.

Responsibilities:
    - Aggregate scenario and test case counts
    - Calculate evaluation statistics (approved, rejected, needs_review)
    - Calculate execution statistics (passed, failed, blocked)
    - Compute pass rate percentage
    - Sum total execution time
    - Collect failed and blocked test details
    - Aggregate JIRA sync metadata (stories, bugs, retests)
    - Generate AI-driven executive summary with key insights
    - Package into ExecutionReport object

Workflow Position:
    ExecutionAgent
        ↓
    ExecutionAnalysisAgent
        ↓
    DefectManagementAgent
        ↓
    ReportAgent (ENHANCED with AI)
        ↓
    END

Inputs:
    - state.requirement: Requirement title for report header
    - state.generated_scenarios: All generated scenarios
    - state.generated_test_cases: All test cases with evaluation_status
    - state.execution_results: Execution outcomes with duration and error messages

Outputs:
    - state.execution_report: ExecutionReport object with all metrics and AI summary

Report Metrics:
    - scenario_count: Total scenarios generated
    - test_case_count: Total test cases generated
    - approved_count: Test cases with APPROVED status
    - needs_review_count: Test cases requiring human review
    - rejected_count: Test cases rejected as duplicates or irrelevant
    - executed_count: Test cases that ran
    - passed_count: Test cases that passed
    - failed_count: Test cases that failed
    - blocked_count: Test cases that errored/skipped
    - pass_rate: passed / executed (0.0 to 1.0)
    - total_execution_seconds: Sum of all execution durations
    - failed_tests: List of TestCaseReportEntry for failed tests
    - blocked_tests: List of TestCaseReportEntry for blocked tests
    - jira_stories_synced: List of JIRA issue keys for synced scenarios
    - jira_bugs_raised: List of JIRA issue keys for bugs
    - jira_retests_required: Count of tests marked for retest
    - executive_summary: AI-generated concise summary with key insights (NEW)

Design Philosophy:
    This agent reflects what happened accurately, including "nothing happened" scenarios.
    A report with all zeros is valid and useful (reveals empty pipeline).
    AI enhancement focuses on extracting key insights and presenting them in
    a content-oriented, keyword-focused manner for stakeholders.

Tolerance:
    Only guards on state.requirement.
    Does not require scenarios, test cases, or executions to exist.
    Gracefully handles empty collections and computes correct zero values.
"""

from backend.agents.base import BaseAgent
from backend.models.execution_report import ExecutionReport, TestCaseReportEntry
from backend.models.state import WorkflowState
from backend.models.test_case import EvaluationStatus, TestCase, TestCaseStatus


class ReportAgent(BaseAgent):
    """
    Execution report aggregation agent with AI-driven content analysis.
    
    This agent aggregates workflow state into a structured report and uses
    LLM to generate keyword-focused executive summaries.
    """
    
    name = "Report Agent"

    def _generate_executive_summary(self, state: WorkflowState, report_metrics: dict) -> str:
        """
        Generate AI-driven executive summary focusing on key insights and business value.
        
        Args:
            state: Workflow state with all execution data
            report_metrics: Dictionary with computed metrics
            
        Returns:
            Concise, keyword-focused executive summary
        """
        try:
            from backend.services.llm import LLMService
            from pydantic import BaseModel, Field
            
            class ExecutiveSummary(BaseModel):
                summary: str = Field(description="3-5 sentence executive summary highlighting key insights, focusing on business value and critical issues. Use keywords from the requirement.")
                key_achievements: list[str] = Field(description="2-3 bullet points of what worked well")
                critical_issues: list[str] = Field(description="2-3 bullet points of failures or blockers that need attention")
                recommendations: list[str] = Field(description="1-2 actionable next steps")
            
            llm = LLMService()
            
            # Build context for the LLM
            requirement_context = f"Requirement: {state.requirement.title}\nDescription: {getattr(state.requirement, 'description', 'N/A')}"
            
            scenarios_context = "\n".join([f"- {s.title}" for s in state.generated_scenarios[:5]])  # Top 5
            if len(state.generated_scenarios) > 5:
                scenarios_context += f"\n... and {len(state.generated_scenarios) - 5} more scenarios"
            
            failed_context = "\n".join([f"- {tc.title}: {tc.status.value}" for tc in state.generated_test_cases if tc.status == TestCaseStatus.FAILED][:5])
            if not failed_context:
                failed_context = "None - all tests passed!"
            
            user_prompt = f"""
{requirement_context}

EXECUTION METRICS:
- Scenarios: {report_metrics['scenario_count']}
- Test Cases: {report_metrics['test_case_count']} (Approved: {report_metrics['approved_count']}, Needs Review: {report_metrics['needs_review_count']})
- Executed: {report_metrics['executed_count']}
- Pass Rate: {report_metrics['pass_rate']:.1%}
- Passed: {report_metrics['passed_count']}
- Failed: {report_metrics['failed_count']}
- Blocked: {report_metrics['blocked_count']}
- Total Time: {report_metrics['total_execution_seconds']:.2f}s

KEY SCENARIOS TESTED:
{scenarios_context}

FAILED TEST CASES:
{failed_context}

Generate a concise, keyword-focused executive summary that:
1. Highlights the BUSINESS VALUE tested (use keywords from the requirement)
2. Emphasizes CRITICAL ISSUES if any failures occurred
3. Provides ACTIONABLE INSIGHTS for stakeholders
4. Focuses on CONTENT and OUTCOMES, not technical implementation details
"""
            
            result = llm.structured_generate(
                user=user_prompt,
                response_model=ExecutiveSummary,
                system="You are an expert QA reporting analyst. You create concise, business-focused executive summaries that highlight key insights and actionable recommendations. You use keywords from the requirements and focus on business value, not technical details."
            )
            
            # Format the summary as markdown
            summary_text = f"""## Executive Summary

{result.summary}

### Key Achievements
{chr(10).join([f'- {item}' for item in result.key_achievements])}

### Critical Issues
{chr(10).join([f'- {item}' for item in result.critical_issues])}

### Recommendations
{chr(10).join([f'- {item}' for item in result.recommendations])}
"""
            
            return summary_text
            
        except Exception as e:
            self.logger.warning(f"Failed to generate AI executive summary: {e}")
            # Fallback to basic summary
            return f"""## Executive Summary

Executed {report_metrics['executed_count']} test cases with a {report_metrics['pass_rate']:.1%} pass rate.
{report_metrics['passed_count']} passed, {report_metrics['failed_count']} failed, {report_metrics['blocked_count']} blocked.
"""

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Generate execution report from workflow state with AI-enhanced summary.
        
        Args:
            state: Workflow state with requirement, scenarios, test cases, and executions
            
        Returns:
            Updated state with execution_report populated (including AI summary)
            
        Raises:
            ValueError: If state.requirement is None
            
        Process:
            1. Map execution results to test cases by ID
            2. Count evaluation statuses (approved, needs_review, rejected)
            3. Count execution statuses (passed, failed, blocked)
            4. Calculate pass rate and total execution time
            5. Collect failed/blocked test details
            6. Aggregate JIRA sync metadata
            7. Generate AI-driven executive summary
            8. Create ExecutionReport and attach to state
        """
        if state.requirement is None:
            raise ValueError(f"{self.name} requires state.requirement to be set")

        state.add_log(f"{self.name} started")

        results_by_test_case_id = {r.test_case_id: r for r in state.execution_results}

        approved = sum(1 for tc in state.generated_test_cases if tc.evaluation_status == EvaluationStatus.APPROVED)
        needs_review = sum(1 for tc in state.generated_test_cases if tc.evaluation_status == EvaluationStatus.NEEDS_REVIEW)
        rejected = sum(1 for tc in state.generated_test_cases if tc.evaluation_status == EvaluationStatus.REJECTED)

        executed = [tc for tc in state.generated_test_cases if tc.status != TestCaseStatus.PENDING]
        passed = [tc for tc in executed if tc.status == TestCaseStatus.PASSED]
        failed = [tc for tc in executed if tc.status == TestCaseStatus.FAILED]
        blocked = [tc for tc in executed if tc.status == TestCaseStatus.BLOCKED]

        def _entry(test_case: TestCase) -> TestCaseReportEntry:
            result = results_by_test_case_id.get(test_case.id)
            return TestCaseReportEntry(
                title=test_case.title,
                status=test_case.status.value,
                duration_seconds=result.duration_seconds if result else None,
                reason=result.error_message if result else None,
            )

        # Collect stories
        stories_list = [s.jira_issue_key for s in state.generated_scenarios if s.jira_issue_key]
        # Collect bugs
        bugs_list = [tc.jira_issue_key for tc in state.generated_test_cases if tc.jira_issue_key]
        # Count retests
        retests_count = sum(1 for tc in state.generated_test_cases if tc.status.value in ["retest_pending", "retest_required"])

        # Prepare metrics for AI summary generation
        report_metrics = {
            "scenario_count": len(state.generated_scenarios),
            "test_case_count": len(state.generated_test_cases),
            "approved_count": approved,
            "needs_review_count": needs_review,
            "rejected_count": rejected,
            "executed_count": len(executed),
            "passed_count": len(passed),
            "failed_count": len(failed),
            "blocked_count": len(blocked),
            "pass_rate": (len(passed) / len(executed)) if executed else 0.0,
            "total_execution_seconds": sum(r.duration_seconds for r in state.execution_results),
        }

        # Generate AI-driven executive summary
        executive_summary = self._generate_executive_summary(state, report_metrics)

        report = ExecutionReport(
            requirement_title=state.requirement.title,
            scenario_count=report_metrics["scenario_count"],
            test_case_count=report_metrics["test_case_count"],
            approved_count=approved,
            needs_review_count=needs_review,
            rejected_count=rejected,
            executed_count=report_metrics["executed_count"],
            passed_count=report_metrics["passed_count"],
            failed_count=report_metrics["failed_count"],
            blocked_count=report_metrics["blocked_count"],
            pass_rate=report_metrics["pass_rate"],
            total_execution_seconds=report_metrics["total_execution_seconds"],
            failed_tests=[_entry(tc) for tc in failed],
            blocked_tests=[_entry(tc) for tc in blocked],
            jira_stories_synced=stories_list,
            jira_bugs_raised=bugs_list,
            jira_retests_required=retests_count,
            executive_summary=executive_summary  # NEW: AI-generated summary
        )

        state.execution_report = report
        state.add_log(f"{self.name} finished: {report.passed_count}/{report.executed_count} passed ({report.pass_rate:.0%})")
        return state