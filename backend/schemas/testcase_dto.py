from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from backend.models.common import Priority
from backend.models.test_case import TestCaseStatus, EvaluationStatus


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
    generated_at: datetime
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    jira_sync_status: str | None = None
    jira_last_synced_at: datetime | None = None


class ScriptUpdatePayload(BaseModel):
    script: str
