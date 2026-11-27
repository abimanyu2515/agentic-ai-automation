"""
ExecutionResult.

References its TestCase by id, same pattern as everywhere else in this
project. ERROR is a distinct status from FAILED on purpose: a test that
ran and its assertion failed (FAILED) means the thing under test has a
real problem worth reporting; a test that couldn't even produce a
verdict - a syntax error in the generated script, a CLI crash, a timeout
- is a tooling problem, not a finding about the application. Collapsing
these into one status would hide which one you're actually looking at.
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"  # couldn't get a verdict at all - not the same as FAILED


class ExecutionResult(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    test_case_id: UUID
    status: ExecutionStatus
    duration_seconds: float = 0.0
    error_message: str | None = None
    screenshot_path: str | None = None
    video_path: str | None = None
    trace_path: str | None = None
    browser_version: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # A real test cycle of this project only, never a batch id (spec 0012).
    test_cycle_id: UUID | None = None
    # Spec 0012: shared by every run of one batch; set by whoever starts the batch.
    batch_id: UUID | None = None
    timeline: list[dict] | None = None
    screenshots: list[str] | None = None
    # Triage and Analysis Fields (populated by ExecutionAnalysisAgent)
    failure_category: str | None = None
    root_cause_summary: str | None = None
    trace_analysis: str | None = None
    screenshot_findings: str | None = None
    suggest_retry: bool | None = None
    retest_pending_candidate: bool | None = None
    # Log analyser evidence (populated by LogAnalysisService)
    failing_step: str | None = None
    evidence: list[str] | None = None
    confidence: float | None = None
    flaky_score: float | None = None
    frontend_error_detected: bool | None = None
    frontend_error_details: str | None = None
    run_history_summary: dict | None = None

    # Jira Bug Link (written by BugReportService when a report is submitted)
    jira_bug_id: str | None = None
    jira_bug_url: str | None = None
    # The bug report drafted for this run (BugReportingAgent / BugReportService)
    bug_report_id: UUID | None = None

    # Spec 0001, AC-4 and AC-8: the project configuration captured when the run was
    # accepted (project_id, target_url, framework, credential_reference). The run uses
    # it even if the project is edited later. Never holds a secret. None for old runs.
    project_config_snapshot: dict[str, str] | None = None