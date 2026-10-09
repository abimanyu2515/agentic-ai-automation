from datetime import datetime, timezone
from uuid import UUID, uuid4
from pydantic import BaseModel, Field


class Document(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    filename: str
    original_filename: str
    mime_type: str
    size: int
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    storage_path: str
    embedding_status: str = "pending"  # "pending", "processing", "completed", "failed"
    vector_collection: str | None = None
    metadata: dict = Field(default_factory=dict)
