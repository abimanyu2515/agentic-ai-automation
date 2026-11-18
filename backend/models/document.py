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
    remote_doc_id: str | None = None  # RAGFlow document ID in the project's dataset (spec 0006)
    error: str | None = None  # why indexing failed or is waiting
    metadata: dict = Field(default_factory=dict)


# Spec 0006, AC-9: documents created before real uploads existed have no file.
OLD_DOCUMENT_ERROR = "file was never uploaded, please upload it again"
