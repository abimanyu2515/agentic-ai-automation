"""The retrieval contract (spec 0006).

Every call takes a `Project` and uses only `project.rag_dataset_id`. No call
accepts a raw dataset ID from a route, the chat, or settings, so one project
can never read or delete another project's knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

# Parse states the indexer understands (spec 0006, AC-3).
PARSE_RUNNING = "running"
PARSE_DONE = "done"
PARSE_FAILED = "failed"


class RetrieverError(Exception):
    """A retriever call failed. `transient` marks connection errors, timeouts,
    and 5xx responses, which are worth retrying (AC-4)."""

    def __init__(self, message: str, transient: bool = False, status_code: int | None = None, code: int | None = None):
        super().__init__(message)
        self.transient = transient
        # The HTTP status and RAGFlow's body `code`, when the call got an answer (spec 0007, AC-13).
        self.status_code = status_code
        self.code = code


class RemoteNotFound(RetrieverError):
    """The remote document or dataset is already gone (spec 0007, AC-13).
    A delete treats it as success. Never transient."""

    def __init__(self, message: str, status_code: int | None = None, code: int | None = None):
        super().__init__(message, transient=False, status_code=status_code, code=code)


class NoDatasetError(RetrieverError):
    """The project has no dataset yet."""


@dataclass
class Chunk:
    chunk_id: str
    text: str
    score: float
    remote_doc_id: str
    title: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class IndexResult:
    remote_doc_id: str


@dataclass
class ParseStatus:
    state: str  # PARSE_RUNNING, PARSE_DONE, or PARSE_FAILED
    message: str = ""


@runtime_checkable
class Retriever(Protocol):
    def is_active(self) -> bool: ...

    def ensure_dataset(self, project) -> str:
        """The project's dataset ID; creates `proj-<project_id>` when it has none.
        The caller persists a new ID with set_project_rag_dataset."""
        ...

    def upload_file(self, project, path: Path, name: str) -> IndexResult:
        """Upload a file to the project's dataset and start parsing it."""
        ...

    def upload_text(self, project, text: str, name: str) -> IndexResult: ...

    def parse_status(self, project, remote_id: str) -> ParseStatus: ...

    def delete(self, project, remote_ids: list[str]) -> None: ...

    def delete_dataset(self, project) -> None: ...

    def search(self, project, query: str, top_k: int) -> list[Chunk]: ...


def dataset_name(project) -> str:
    return f"proj-{project.id}"


def require_dataset(project) -> str:
    dataset_id = getattr(project, "rag_dataset_id", None)
    if not dataset_id:
        raise NoDatasetError(f"project {project.id} has no knowledge dataset")
    return dataset_id
