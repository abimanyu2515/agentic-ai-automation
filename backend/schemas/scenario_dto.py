from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from backend.models.common import Priority


class ScenarioUpdate(BaseModel):
    scenario_name: str | None = None
    description: str | None = None
    priority: Priority | None = None
    approved: bool | None = None
    rejected: bool | None = None


class ScenarioResponse(BaseModel):
    id: UUID
    requirement_id: UUID
    scenario_ref_id: str | None = None  # US01, US02, US03...
    scenario_name: str
    description: str
    priority: Priority
    confidence: float
    approved: bool
    rejected: bool
    generated_at: datetime
    reviewer: str | None = None
    approved_at: datetime | None = None
    jira_issue_key: str | None = None
    jira_issue_id: str | None = None
    jira_issue_url: str | None = None
    jira_sync_status: str | None = None
    jira_last_synced_at: datetime | None = None
    last_jira_sync_at: datetime | None = None
    last_jira_sync_status: str | None = None
    last_jira_sync_error: str | None = None
    jira_sync_retry_count: int = 0
