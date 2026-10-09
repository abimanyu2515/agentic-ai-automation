from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field
from backend.models.requirement import RequirementSource


class RequirementCreate(BaseModel):
    title: str
    description: str
    priority: str = "medium"
    business_domain: str = "general"
    release_id: UUID | None = None


class RequirementResponse(BaseModel):
    id: UUID
    title: str
    description: str
    source: RequirementSource
    uploaded_at: datetime
    priority: str
    business_domain: str
    attachments: list[str]
    original_filename: str | None = None
    requirement_id: str | None = None
    requirement_title: str | None = None
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    jira_sync_status: str | None = None
    jira_last_synced_at: datetime | None = None
    release_id: UUID | None = None
