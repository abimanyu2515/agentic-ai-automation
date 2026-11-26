from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from backend.models.common import Priority
from backend.models.test_case import TestCaseStatus, EvaluationStatus, ScriptValidationStatus


class TestCaseUpdate(BaseModel):
    title: str | None = None
    preconditions: list[str] | None = None
    steps: list[str] | None = None
    expected_result: str | None = None
    priority: Priority | None = None
    status: TestCaseStatus | None = None
    confidence: float | None = None
    evaluation_status: EvaluationStatus | None = None
    evaluation_reason: str | None = None


class ScriptHealResponse(BaseModel):
    """One script repair from the RCA (spec 0011, AC-12). Scripts hold object repository keys."""

    id: UUID
    test_case_id: UUID
    status: str
    old_script: str
    new_script: str
    rca_category: str
    root_cause_summary: str
    failing_step: str | None = None
    reason: str
    execution_id: UUID | None = None
    created_at: datetime
    decided_by: UUID | None = None
    decided_at: datetime | None = None


class TestCaseResponse(BaseModel):
    id: UUID
    scenario_id: UUID
    test_case_ref_id: str | None = None  # US01-TC01, US01-TC02...
    title: str
    preconditions: list[str]
    steps: list[str]
    expected_result: str
    priority: Priority
    status: TestCaseStatus
    confidence: float
    evaluation_status: EvaluationStatus
    evaluation_reason: str | None
    playwright_script: str | None
    script_validation_status: ScriptValidationStatus = ScriptValidationStatus.UNVERIFIED
    script_validation_attempts: int = 0
    script_validation_completed_at: datetime | None = None
    script_validation_diagnostic: str | None = None
    groundedness_score: float | None = None
    unsupported_claims: list[str] = Field(default_factory=list)
    generated_at: datetime
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    jira_sync_status: str | None = None
    jira_last_synced_at: datetime | None = None
    test_data_sets: list[dict[str, str]] = Field(default_factory=list)
    # Spec 0011, AC-12: the test case's latest script heal, or null.
    latest_script_heal: ScriptHealResponse | None = None


class ScriptUpdatePayload(BaseModel):
    script: str
