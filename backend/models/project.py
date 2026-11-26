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
    owner_id: UUID | None = None
    created_by: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="")
    line_of_business: str = Field(default="general")
    framework: str = Field(default="playwright")
    jira_project_key: str | None = None
    target_url: str | None = None
    target_username: str | None = None
    target_password_enc: str | None = None
    is_safe_test_environment: bool = False
    # Spec 0006: this project's own RAGFlow dataset. Set once by the knowledge
    # indexer through set_project_rag_dataset; never shared between projects.
    rag_dataset_id: str | None = None
    # Bug Reporting Agent: submit drafts to Jira without review when the
    # triage confidence reaches the threshold. Off by default.
    bug_auto_submit: bool = False
    bug_auto_submit_min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    # Spec 0011: heal broken object repository locators after a run. On by default.
    self_healing_enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    requirements: list[UUID] = Field(default_factory=list)
    is_deleted: bool = False
