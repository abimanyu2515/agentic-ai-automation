from datetime import datetime, timezone
from uuid import UUID, uuid4
from pydantic import BaseModel, Field

class TestCycle(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    release_id: UUID
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    status: str = "Active"  # Active, Completed
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
