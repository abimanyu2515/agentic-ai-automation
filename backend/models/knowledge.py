"""KnowledgeEntry: one indexed project source other than an uploaded file (spec 0006).

One row per (project_id, source_type, source_ref). `content_hash` is the
sha256 of the exact text uploaded, so unchanged content is never sent again.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# Source types a KnowledgeEntry can hold. `document` is a Document row, not an entry.
ENTRY_SOURCE_TYPES = ("jira_story", "test_case", "execution_failure", "defect", "locator")

ENTRY_INDEXED = "indexed"
ENTRY_FAILED = "failed"


class KnowledgeEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source_type: str
    source_ref: str
    title: str
    remote_doc_id: str | None = None
    content_hash: str
    status: str = ENTRY_INDEXED  # "indexed" or "failed"
    error: str | None = None
    indexed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Pending RAGFlow deletes (spec 0007). A row is kept until RAGFlow confirms the
# delete or says the target is already gone; there is no other state.
REMOTE_DELETE_DOCUMENT = "document"
REMOTE_DELETE_DATASET = "dataset"
REMOTE_DELETE_REASONS = (
    "document_deleted",
    "project_deleted",
    "entry_removed",
    "entry_replaced",
    "upload_raced_delete",
    "parse_failed",
    "duplicate_dataset",
)
REMOTE_DELETE_ERROR_CHARS = 500


class KnowledgeRemoteDelete(BaseModel):
    """One remote target still to delete: one RAGFlow document or one whole dataset.

    `project_id` is a plain value with no foreign key, so the row outlives its
    project. The delete only ever targets the `dataset_id` saved here.
    """

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    kind: str  # REMOTE_DELETE_DOCUMENT or REMOTE_DELETE_DATASET
    dataset_id: str
    remote_doc_id: str | None = None  # set for documents, None for datasets
    reason: str
    attempts: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_attempt_at: datetime | None = None
