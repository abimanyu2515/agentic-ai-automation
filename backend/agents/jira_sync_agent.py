"""
JiraSyncAgent

Purpose:
    Synchronizes workflow artifacts (scenarios, test cases, execution results) with JIRA.
    Creates user stories for scenarios and bug tickets for test failures.
    Implements duplicate detection to avoid creating redundant JIRA issues.

Responsibilities:
    - Sync approved scenarios to JIRA as Story issues
    - Sync failed test executions to JIRA as Bug issues
    - Detect duplicate JIRA issues using label-based JQL queries
    - Link execution results to existing bug tickets via comments
    - Transition bug tickets through workflow states (In Progress, Ready for Testing)
    - Upload execution artifacts (screenshots) to JIRA issues
    - Update workflow state with JIRA issue keys and URLs
    - Log audit trail for JIRA operations

Workflow Position:
    Triggered via dedicated operation routing (not in main pipeline):
    
    Operation: SYNC_USER_STORY
    SupervisorAgent → JiraSyncAgent → END
    
    Operation: SYNC_BUG
    ExecutionAgent → ExecutionAnalysisAgent → DefectManagementAgent → ReportAgent → END
    (Alternate path: SupervisorAgent → JiraSyncAgent for manual sync)
    
    Operation: RETEST_BUG
    SupervisorAgent → JiraSyncAgent → END

Inputs (Operation-Dependent):
    - SYNC_USER_STORY:
        - state.generated_scenarios: Approved scenarios to sync
    - SYNC_BUG:
        - state.execution_results: Failed execution results
        - state.generated_test_cases: Test case details for bug description
    - RETEST_BUG:
        - Test cases with open bugs (status check via JIRA API)

Outputs:
    - scenario.jira_issue_key: JIRA Story key
    - scenario.jira_issue_url: Full JIRA issue URL
    - scenario.jira_sync_status: "synced"
    - test_case.jira_issue_key: JIRA Bug key
    - test_case.jira_issue_url: Full JIRA issue URL
    - test_case.jira_sync_status: "created" or "linked"
    - test_case.status: Updated to RETEST_PENDING for bugs
    - Audit log entries via log_audit()

Duplicate Detection:
    Uses label-based tagging strategy:
    - Scenario: Label "scenario_{scenario.id}"
    - Test Case: Label "test_{test_case.id}"
    
    JQL query: project = 'QA' AND labels = 'scenario_abc123' AND status != 'Closed'
    
    If existing issue found:
    - Reuse existing issue key
    - Add comment with new execution details
    - Transition to appropriate state

JIRA Issue Creation:
    - User Story:
        - Summary: "Scenario Story: {scenario_name}"
        - Description: Scenario description + reviewer info
        - Issue Type: Story
        - Labels: ["platform_sync", "scenario_{id}"]
        
    - Bug:
        - Summary: "Bug: Test Case Failure - {test_case_title}"
        - Description: Test case details + runtime error log
        - Issue Type: Bug
        - Labels: ["platform_sync", "test_{id}", "bug"]
        - Attachments: Screenshots from execution artifacts

Dependencies:
    - JiraService: JIRA REST API client
    - ProjectRepository: Database access for scenarios and test cases
    - AuditService: Audit logging for JIRA operations
    - VaultService (indirect): Credentials for JIRA API

Design Decision:
    Duplicate detection is critical to avoid JIRA spam.
    Labels provide reliable correlation across workflow runs.
    Comments link multiple execution failures to single bug ticket.
"""

import logging
import os
from datetime import datetime, timezone
from uuid import UUID
from backend.agents.base import BaseAgent
from backend.models.state import WorkflowState
from backend.models.test_case import TestCaseStatus
from backend.services.jira_service import JiraService
from backend.repository.project_repository import get_project_repository
from backend.services.audit_service import log_audit

logger = logging.getLogger("backend.agents.jira_sync_agent")


class JiraSyncAgent(BaseAgent):
    """
    JIRA integration agent for workflow artifact synchronization.
    
    This agent does not call LLMs. It orchestrates JIRA API calls
    and maintains issue linkage.
    """
    
    name = "Jira Sync Agent"

    def __init__(self, jira_service: JiraService | None = None):
        """
        Initialize JIRA sync agent.
        
        Args:
            jira_service: JIRA service instance (injected for testing)
        """
        self.jira_service = jira_service or JiraService()

    def run(self, state: WorkflowState, config: dict | None = None) -> WorkflowState:
        """
        Synchronize workflow artifacts with JIRA.
        
        Args:
            state: Workflow state with scenarios, test cases, and execution results
            config: LangGraph config containing operation and user_id
            
        Returns:
            Updated state with JIRA issue keys and URLs populated
            
        Process:
            1. Extract operation type from config (sync_user_story, sync_bug, retest_bug)
            2. Resolve project context and JIRA project key
            3. Route to appropriate sync handler:
                - sync_user_story: _sync_scenarios_as_stories()
                - sync_bug: _sync_failures_as_bugs()
                - retest_bug: _process_bug_retests()
            4. Log completion
        """
        state.add_log(f"{self.name} started")
        
        configurable = config.get("configurable", {}) if config else {}
        op = configurable.get("operation")
        user_id_str = configurable.get("user_id")
        user_id = UUID(user_id_str) if user_id_str else None

        repo = get_project_repository()
        db_session = getattr(repo, "session", None)

        # Retrieve project context
        project = None
        project_id = None
        if state.requirement:
            projects = repo.list_projects()
            project = next((p for p in projects if state.requirement.id in p.requirements), None)
            if project:
                project_id = project.id

        # Determine target JIRA project key
        jira_project_key = None
        if project:
            jira_project_key = getattr(project, "jira_project_key", None)
        if not jira_project_key:
            jira_project_key = self.jira_service.config.project_key or "QA"

        if op == "sync_user_story":
            self._sync_scenarios_as_stories(state, repo, project_id, jira_project_key, user_id, db_session)
        elif op == "sync_bug":
            self._sync_failures_as_bugs(state, repo, project_id, jira_project_key, user_id, db_session)
        elif op == "retest_bug":
            self._process_bug_retests(state, repo, project_id, user_id, db_session)
        else:
            state.add_log(f"{self.name}: No matching operation '{op}' mapped.")

        state.add_log(f"{self.name} completed successfully")
        return state

    def _sync_scenarios_as_stories(self, state: WorkflowState, repo, project_id, jira_project_key, user_id, db_session):
        """Synchronizes approved scenarios/backlog items to Jira as Stories."""
        approved_scenarios = [s for s in state.generated_scenarios if s.approved]
        
        for s in approved_scenarios:
            label_tag = f"scenario_{s.id}"
            jql = f"project = '{jira_project_key}' AND labels = '{label_tag}'"
            
            # Duplicate detection
            existing = self.jira_service.search_issues(jql)
            if existing:
                issue = existing[0]
                logger.info(f"Reusing existing Jira issue {issue['key']} for scenario {s.id}")
                s.jira_issue_key = issue["key"]
                s.jira_issue_url = f"{self.jira_service.config.base_url.rstrip('/')}/browse/{issue['key']}"
                s.jira_sync_status = "synced"
                s.jira_last_synced_at = datetime.now(timezone.utc)
                repo.update_scenario(s)
                state.add_log(f"Linked existing Jira Story: {s.jira_issue_key}")
                continue
                
            # Create a new Jira Story
            summary = f"Scenario Story: {s.scenario_name}"
            desc = (
                f"User Story Description:\n{s.description}\n\n"
                f"Orchestrated by Platform.\n"
                f"Reviewer: {s.reviewer or 'Auto-approved'}"
            )
            labels = ["platform_sync", label_tag]
            
            issue = self.jira_service.create_issue(
                summary=summary,
                description=desc,
                issue_type=self.jira_service.config.default_issue_type or "Story",
                project_key=jira_project_key,
                labels=labels,
                assignee_email=s.reviewer
            )
            
            if issue:
                s.jira_issue_key = issue["key"]
                s.jira_issue_url = issue["url"]
                s.jira_sync_status = "synced"
                s.jira_last_synced_at = datetime.now(timezone.utc)
                repo.update_scenario(s)
                state.add_log(f"Synced User Story to Jira: {s.jira_issue_key}")
                
                # Write to platform audit logs
                if db_session:
                    log_audit(
                        db_session,
                        user_id,
                        project_id,
                        action="JIRA_CREATE",
                        module="SCENARIO",
                        field_changes={"jira_issue_key": s.jira_issue_key, "scenario_id": str(s.id)}
                    )
            else:
                logger.error(f"Failed to create Jira Story for scenario {s.id}")

    def _sync_failures_as_bugs(self, state: WorkflowState, repo, project_id, jira_project_key, user_id, db_session):
        """Processes failed test cases and raises Bugs in Jira."""
        failed_results = [r for r in state.execution_results if r.status.value.lower() == "failed"]
        
        for res in failed_results:
            tc = repo.get_test_case(res.test_case_id)
            if not tc:
                continue
                
            label_tag = f"test_{tc.id}"
            jql = f"project = '{jira_project_key}' AND labels = '{label_tag}' AND status != 'Closed'"
            
            existing = self.jira_service.search_issues(jql)
            if existing:
                issue = existing[0]
                key = issue["key"]
                logger.info(f"Reusing existing open bug {key} for failed test case {tc.id}")
                
                # Link execution via comments
                comment = (
                    f"Test execution failed again at {res.executed_at.isoformat() if isinstance(res.executed_at, datetime) else res.executed_at}.\n"
                    f"Error Message: {res.error_message or 'No specific error log provided.'}"
                )
                self.jira_service.add_comment(key, comment)
                
                # Move to Ready for Testing or In Progress
                self.jira_service.transition_issue(key, "In Progress")
                
                tc.jira_issue_key = key
                tc.jira_issue_url = f"{self.jira_service.config.base_url.rstrip('/')}/browse/{key}"
                tc.jira_sync_status = "linked"
                tc.jira_last_synced_at = datetime.now(timezone.utc)
                tc.status = TestCaseStatus.RETEST_PENDING
                repo.update_test_case(tc)
                
                state.add_log(f"Linked failed run to existing JIRA Bug: {key}")
                continue
                
            # Create a new Bug ticket in Jira
            summary = f"Bug: Test Case Failure - {tc.title}"
            desc = (
                f"Test execution failed.\n\n"
                f"Test Case Title: {tc.title}\n"
                f"Expected Result: {tc.expected_result}\n\n"
                f"Runtime Error log:\n{res.error_message or 'No error message caught.'}"
            )
            labels = ["platform_sync", label_tag, "bug"]
            
            issue = self.jira_service.create_issue(
                summary=summary,
                description=desc,
                issue_type="Bug",
                project_key=jira_project_key,
                labels=labels
            )
            
            if issue:
                key = issue["key"]
                tc.jira_issue_key = key
                tc.jira_issue_url = issue["url"]
                tc.jira_sync_status = "created"
                tc.jira_last_synced_at = datetime.now(timezone.utc)
                tc.status = TestCaseStatus.RETEST_PENDING
                repo.update_test_case(tc)
                
                # Attach runner screenshot if captured
                if res.screenshot_path and os.path.exists(res.screenshot_path):
                    try:
                        with open(res.screenshot_path, "rb") as f:
                            self.jira_service.upload_attachment(key, "screenshot.png", f.read(), "image/png")
                    except Exception as e:
                        logger.error(f"Failed to upload screenshot to Jira: {e}")
                
                # Attach HTML execution report if available
                # Reports are stored in backend/playwrightt/public/artifacts/{execution_id}/report.html
                from pathlib import Path
                report_path = Path("backend/playwrightt/public/artifacts") / str(res.id) / "report.html"
                if report_path.exists():
                    try:
                        with open(report_path, "rb") as f:
                            self.jira_service.upload_attachment(key, f"execution_report_{res.id}.html", f.read(), "text/html")
                        logger.info(f"Uploaded execution report to Jira {key}")
                    except Exception as e:
                        logger.error(f"Failed to upload execution report to Jira: {e}")
                
                state.add_log(f"Created JIRA Bug {key} for failing test run.")
                
                if db_session:
                    log_audit(
                        db_session,
                        user_id,
                        project_id,
                        action="JIRA_CREATE",
                        module="TESTCASE",
                        field_changes={"jira_issue_key": key, "test_case_id": str(tc.id)}
                    )
            else:
                logger.error(f"Failed to create Jira Bug for test case {tc.id}")

    def _process_bug_retests(self, state: WorkflowState, repo, project_id, user_id, db_session):
        """Scans test cases with open bugs and checks if they are ready for retest in Jira."""
        # This fallback polling is used if webhooks are not triggered
        pass
