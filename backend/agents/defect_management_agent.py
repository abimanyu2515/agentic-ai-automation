"""
DefectManagementAgent

Purpose:
    Manages JIRA bug ticket lifecycle for verified Product Bug failures.
    Detects duplicate bugs using LLM-generated JQL queries.
    Creates new bug tickets or links to existing ones.

Responsibilities:
    - Filter for Product Bug failures (from ExecutionAnalysisAgent)
    - Generate smart JQL search queries via LLM to detect duplicate bugs
    - Search JIRA for existing open bugs matching failure pattern
    - Link execution to existing bug via comments if duplicate found
    - Create new JIRA bug ticket if no duplicate exists
    - Upload execution artifacts (screenshots) to JIRA attachments
    - Update test case with JIRA issue linkage
    - Log defect management actions

Workflow Position:
    ExecutionAgent
        ↓
    ExecutionAnalysisAgent (triages failure_category)
        ↓
    DefectManagementAgent (only processes Product Bugs)
        ↓
    ReportAgent

Inputs:
    - state.execution_results: Latest execution result with failure_category
    - state.generated_test_cases: Test case details for bug description

Outputs:
    - execution_result.jira_bug_id: JIRA Bug issue key
    - execution_result.jira_bug_url: Full JIRA issue URL
    - test_case.jira_issue_key: JIRA Bug key
    - test_case.jira_issue_url: Full JIRA issue URL
    - test_case.jira_sync_status: "created" or "linked"
    - test_case.jira_last_synced_at: Timestamp

LLM-Powered Duplicate Detection:
    Instead of label-based matching (like JiraSyncAgent), this agent uses LLM
    to generate semantic JQL queries based on:
    - Test case title
    - Failure error message
    - Trace log patterns
    
    Example LQL generated:
    "project = 'QA' AND issuetype = Bug AND status != Closed AND 
     (summary ~ 'login' OR description ~ 'authentication failed')"

Duplicate Handling:
    If duplicate bug found:
    1. Retrieve existing issue
    2. Add comment with new execution timestamp and error message
    3. Link execution_result to existing bug
    4. Update test_case with JIRA linkage
    5. Log "duplicate defect detected"

New Bug Creation:
    If no duplicate found:
    1. Generate bug title via LLM
    2. Generate bug description via LLM (includes test steps, expected result, error logs)
    3. Create JIRA issue with type=Bug
    4. Apply labels: ["platform_sync", "test_{id}", "bug"]
    5. Upload screenshot artifact if available
    6. Link execution_result and test_case to new bug
    7. Log "created JIRA Bug"

Dependencies:
    - JiraService: JIRA API client for search, create, comment, attach
    - LLMService: For JQL generation and bug description formatting

Design Philosophy:
    Only verified Product Bugs get JIRA tickets.
    Environment issues and flaky tests are filtered out by ExecutionAnalysisAgent.
    Semantic duplicate detection reduces JIRA spam better than rigid label matching.
"""

import os
import logging
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.models.execution_result import ExecutionStatus
from backend.services.llm import LLMService
from backend.services.jira_service import JiraService
from backend.utils.prompts import load_prompt

logger = logging.getLogger("backend.agents.defect_management_agent")


# ==========================================================
# LLM Output Schema
# ==========================================================

class _DefectSearchResponse(BaseModel):
    """LLM-generated defect search query and bug metadata."""
    jira_search_query: str = Field(description="JQL search term to look up duplicate bugs")
    is_duplicate_candidate: bool = Field(description="True if we suspect a duplicate bug exists")
    bug_title: str = Field(description="Proposed title for the JIRA Bug if not a duplicate")
    bug_description: str = Field(description="Proposed description for the JIRA Bug if not a duplicate")


class DefectManagementAgent(BaseAgent):
    """
    JIRA defect lifecycle management agent with semantic duplicate detection.
    
    Uses LLM-generated JQL queries to find existing bugs before creating new ones.
    """
    
    name = "Defect Management Agent"

    def __init__(self, llm_service: LLMService | None = None, jira_service: JiraService | None = None):
        """
        Initialize defect management agent.
        
        Args:
            llm_service: LLM service instance (injected for testing)
            jira_service: JIRA service instance (injected for testing)
        """
        self.llm_service = llm_service or LLMService()
        self.jira_service = jira_service or JiraService()

    def run(self, state: WorkflowState) -> WorkflowState:
        """
        Manage JIRA bug lifecycle for Product Bug failures.
        
        Args:
            state: Workflow state with execution results and test cases
            
        Returns:
            Updated state with JIRA bug linkage populated
            
        Process:
            1. Skip if no execution results exist
            2. Get latest execution result
            3. Skip if status is PASSED or failure_category is not "Product Bug"
            4. Find corresponding test case
            5. Generate JQL search query via LLM
            6. Search JIRA for duplicate bugs
            7. If duplicate found:
                - Add comment with new execution details
                - Link execution and test case to existing bug
            8. If no duplicate:
                - Create new JIRA Bug issue
                - Upload screenshot artifact
                - Link execution and test case to new bug
        """
        if not state.execution_results:
            state.add_log(f"{self.name}: no execution results found.")
            return state

        result = state.execution_results[-1]
        
        # We only file bugs for verified Product Bugs
        if result.status == ExecutionStatus.PASSED or result.failure_category != "Product Bug":
            state.add_log(f"{self.name}: skip bug sync as status is {result.status.value} / category is {result.failure_category}")
            return state

        state.add_log(f"{self.name} started processing defect for execution {result.id}")

        # Find the test case in state
        tc = next((t for t in state.generated_test_cases if t.id == result.test_case_id), None)
        tc_title = tc.title if tc else "Unknown Test Case"
        tc_steps = "\n".join(f"- {s}" for s in tc.steps) if tc else "N/A"

        prompt = load_prompt(
            "defect_agent_prompt.txt",
            title=tc_title,
            steps=tc_steps,
            failure_message=result.error_message or "Unknown failure",
            trace_log=result.error_message or "Unknown failure"
        )

        try:
            analysis = self.llm_service.structured_generate(
                user=prompt,
                response_model=_DefectSearchResponse
            )

            # Query Jira for duplicates using the JQL generated by LLM
            existing = self.jira_service.search_issues(analysis.jira_search_query)
            if existing:
                issue = existing[0]
                key = issue["key"]
                state.add_log(f"{self.name}: duplicate defect detected: {key}")
                
                # Link execution via comment
                comment = (
                    f"Test execution failed again at {result.executed_at.isoformat()}.\n"
                    f"Error Message: {result.error_message or 'No specific error log provided.'}"
                )
                self.jira_service.add_comment(key, comment)
                
                result.jira_bug_id = key
                result.jira_bug_url = f"{self.jira_service.config.base_url.rstrip('/')}/browse/{key}"
                
                if tc:
                    tc.jira_issue_key = key
                    tc.jira_issue_url = result.jira_bug_url
                    tc.jira_sync_status = "linked"
                    tc.jira_last_synced_at = datetime.now(timezone.utc)
            else:
                # Create a new Bug in Jira
                labels = ["platform_sync", f"test_{tc.id if tc else 'unknown'}", "bug"]
                issue = self.jira_service.create_issue(
                    summary=analysis.bug_title,
                    description=analysis.bug_description,
                    issue_type="Bug",
                    labels=labels
                )
                
                if issue:
                    key = issue["key"]
                    result.jira_bug_id = key
                    result.jira_bug_url = issue["url"]
                    
                    if tc:
                        tc.jira_issue_key = key
                        tc.jira_issue_url = issue["url"]
                        tc.jira_sync_status = "created"
                        tc.jira_last_synced_at = datetime.now(timezone.utc)
                    
                    # Attach runner screenshot if captured
                    if result.screenshot_path and os.path.exists(result.screenshot_path):
                        try:
                            with open(result.screenshot_path, "rb") as f:
                                self.jira_service.upload_attachment(key, "screenshot.png", f.read(), "image/png")
                        except Exception as attach_err:
                            logger.error(f"Failed to upload attachment: {attach_err}")
                            
                    state.add_log(f"{self.name}: created JIRA Bug: {key}")

        except Exception as exc:
            state.add_log(f"{self.name} failed: {exc}")

        return state
