"""
Requirement model.

A Requirement is immutable once created — nothing downstream should be able
to mutate the original input. If a requirement needs changing, create a new
one; don't patch this one in place. That keeps every agent's reference to
"the requirement it saw" trustworthy for the life of a run.
"""

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class RequirementSource(str, Enum):
    PDF = "pdf"
    JIRA = "jira"
    MANUAL = "manual"


class Requirement(BaseModel):
    model_config = {"frozen": True}

    id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    source: RequirementSource = RequirementSource.MANUAL
    uploaded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    original_filename: str | None = None
    requirement_id: str | None = None
    requirement_title: str | None = None
    priority: str = "medium"
    business_domain: str = "general"
    attachments: list[str] = Field(default_factory=list)
    feature_mapping: str | None = None
    jira_issue_key: str | None = None
    jira_issue_url: str | None = None
    jira_sync_status: str | None = None
    jira_last_synced_at: datetime | None = None
    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    backlog: dict | None = None
    release_id: UUID | None = None