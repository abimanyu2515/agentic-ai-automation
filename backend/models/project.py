"""
Project model.

A project acts as the parent container for a collection of requirements.
It owns requirement IDs, but requirements remain unaware of their project parent.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Project(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="")
    line_of_business: str = Field(default="general")
    framework: str = Field(default="playwright")
    jira_project_key: str | None = None
    target_url: str | None = "https://adactinhotelapp.com/"
    target_username: str | None = None
    target_password_enc: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    requirements: list[UUID] = Field(default_factory=list)

