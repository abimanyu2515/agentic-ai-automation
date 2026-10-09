"""
Scenario model.

Unlike Requirement, a Scenario is mutable — specifically its `approved`
field, which flips from False to True (or gets rejected) during human
review. Everything else about it should be treated as generated-once,
but we don't freeze the whole model just for one field's sake.

It references its parent Requirement by id, not by embedding the object.
Scenario shouldn't need to know anything about Requirement's fields —
just which one it came from.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4
from datetime import datetime, timezone

from pydantic import BaseModel, Field, model_validator

from backend.models.common import Priority


class Scenario(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    requirement_id: UUID
    scenario_ref_id: str | None = None  # US01, US02, US03... (auto-generated)
    scenario_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    priority: Priority = Priority.MEDIUM
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    approved: bool = False
    rejected: bool = False
    generated_at: datetime = Field(
    default_factory=lambda: datetime.now(timezone.utc)
    )
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
    path_type: str = "happy_path"
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_legacy_fields(self) -> "Scenario":
        if self.last_jira_sync_status is None and self.jira_sync_status is not None:
            self.last_jira_sync_status = self.jira_sync_status
        elif self.last_jira_sync_status is not None and self.jira_sync_status is None:
            self.jira_sync_status = self.last_jira_sync_status
            
        if self.last_jira_sync_at is None and self.jira_last_synced_at is not None:
            self.last_jira_sync_at = self.jira_last_synced_at
        elif self.last_jira_sync_at is not None and self.jira_last_synced_at is None:
            self.jira_last_synced_at = self.last_jira_sync_at
        return self