"""
TestCase model.

References its parent Scenario by id, same pattern as Scenario -> Requirement.
Mutable, because `status` moves from PENDING to PASSED/FAILED/BLOCKED once
ExecutionAgent actually runs it, and `evaluation_status`/`confidence` are
written by EvaluationAgent after generation.

`status` and `evaluation_status` are deliberately separate fields, not one
combined one: `status` is about *execution outcome* (did it pass when
actually run through Playwright), `evaluation_status` is about *quality
approval* (was this test case good enough to proceed at all). A test
case can be evaluation-approved and never executed yet, or rejected at
evaluation and therefore never reach execution - conflating them would
make it impossible to represent either state cleanly.

`status` reflects only the most recent execution. Full execution history
(a test case can be run more than once) lives in
WorkflowState.execution_results, matched by test_case_id - this field is
just a convenient at-a-glance summary, not the source of truth.
"""

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from backend.models.common import Priority


class TestCaseStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    RETEST_PENDING = "retest_pending"
    RETEST_REQUIRED = "retest_required"


class EvaluationStatus(str, Enum):
    PENDING = "pending"          # not yet evaluated
    APPROVED = "approved"        # confidence >= threshold
    NEEDS_REVIEW = "needs_review"  # below threshold, not obviously broken
    REJECTED = "rejected"        # duplicate, or evaluation judged it unusable


class TestCase(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    scenario_id: UUID
    test_case_ref_id: str | None = None  # US01-TC01, US01-TC02... (auto-generated)
    title: str = Field(min_length=1, max_length=200)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(min_length=1)
    expected_result: str = Field(min_length=1)
    priority: Priority = Priority.MEDIUM
    status: TestCaseStatus = TestCaseStatus.PENDING
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    evaluation_status: EvaluationStatus = EvaluationStatus.PENDING
    evaluation_reason: str | None = None
    playwright_script: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reviewer: str | None = None
    approved_at: datetime | None = None
    is_frozen: bool = False
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    jira_sync_status: str | None = None
    jira_last_synced_at: datetime | None = None
    test_data: dict[str, str] = Field(default_factory=dict)
    execution_type: str = "UI Automation"
    automation_framework: str = "playwright"
