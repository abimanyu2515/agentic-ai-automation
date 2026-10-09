from datetime import datetime
from uuid import UUID, uuid4
from pydantic import BaseModel, Field

class Release(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    status: str = "Active"  # Active, Completed, Planned
    start_date: datetime | None = None
    end_date: datetime | None = None
