from datetime import datetime
from uuid import UUID
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    line_of_business: str = "general"
    framework: str
    jira_project_key: str | None = None
    target_url: str = Field(min_length=1)


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str
    line_of_business: str
    framework: str
    created_at: datetime
    requirements: list[UUID]
    jira_project_key: str | None = None
    target_url: str | None = None
    target_username: str | None = None
    readiness: dict[str, Any]
