"""Test retrievers (spec 0006).

InMemoryRetriever keeps datasets in a dict and searches by keyword overlap.
NullRetriever is inactive: it is what `get_retriever()` returns when RAG is
disabled, and it never stores or returns anything.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from uuid import uuid4

from backend.services.retrieval.base import (
    PARSE_DONE,
    Chunk,
    IndexResult,
    ParseStatus,
    RemoteNotFound,
    RetrieverError,
    require_dataset,
)

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


class InMemoryRetriever:
    def __init__(self, chunk_chars: int = 400):
        self.chunk_chars = chunk_chars
        self.datasets: dict[str, dict[str, dict]] = {}  # dataset_id -> remote_id -> {name, chunks}
        self.created_datasets: list[str] = []
        self.parse_states: dict[str, ParseStatus] = {}
        self._lock = threading.Lock()

    def is_active(self) -> bool:
        return True

    def ensure_dataset(self, project) -> str:
        if getattr(project, "rag_dataset_id", None):
            return project.rag_dataset_id
        with self._lock:
            dataset_id = f"ds-{uuid4().hex[:12]}"
            self.datasets[dataset_id] = {}
            self.created_datasets.append(dataset_id)
        return dataset_id

    def _dataset(self, project) -> dict:
        dataset_id = require_dataset(project)
        if dataset_id not in self.datasets:
            raise RetrieverError(f"dataset {dataset_id} does not exist")
        return self.datasets[dataset_id]

    def _store(self, project, text: str, name: str) -> IndexResult:
        dataset = self._dataset(project)
        remote_id = f"doc-{uuid4().hex[:12]}"
        pieces = [text[i:i + self.chunk_chars] for i in range(0, len(text), self.chunk_chars)] or [""]
        dataset[remote_id] = {"name": name, "chunks": pieces}
        return IndexResult(remote_doc_id=remote_id)

    def upload_file(self, project, path: Path, name: str) -> IndexResult:
        return self._store(project, Path(path).read_text(encoding="utf-8", errors="replace"), name)

    def upload_text(self, project, text: str, name: str) -> IndexResult:
        return self._store(project, text, name)

    def parse_status(self, project, remote_id: str) -> ParseStatus:
        return self.parse_states.get(remote_id, ParseStatus(PARSE_DONE))

    def delete(self, project, remote_ids: list[str]) -> None:
        """Deletes the known IDs, then raises RemoteNotFound when any ID (or the
        dataset itself) was unknown, like RAGFlow's "already gone" (spec 0007)."""
        dataset_id = require_dataset(project)
        dataset = self.datasets.get(dataset_id)
        if dataset is None:
            raise RemoteNotFound(f"dataset {dataset_id} does not exist")
        missing = [remote_id for remote_id in remote_ids if dataset.pop(remote_id, None) is None]
        if missing:
            raise RemoteNotFound(f"documents not found: {missing}")

    def delete_dataset(self, project) -> None:
        dataset_id = require_dataset(project)
        if self.datasets.pop(dataset_id, None) is None:
            raise RemoteNotFound(f"dataset {dataset_id} does not exist")

    def search(self, project, query: str, top_k: int) -> list[Chunk]:
        dataset = self._dataset(project)
        wanted = _words(query)
        scored = []
        for remote_id, doc in dataset.items():
            for index, text in enumerate(doc["chunks"]):
                overlap = len(wanted & _words(text))
                if overlap:
                    scored.append(
                        Chunk(
                            chunk_id=f"{remote_id}:{index}",
                            text=text,
                            score=overlap / max(len(wanted), 1),
                            remote_doc_id=remote_id,
                            title=doc["name"],
                        )
                    )
        scored.sort(key=lambda chunk: chunk.score, reverse=True)
        return scored[:top_k]


class NullRetriever:
    """Inactive retriever: RAG is disabled."""

    def is_active(self) -> bool:
        return False

    def _off(self, *args, **kwargs):
        raise RetrieverError("the knowledge base is disabled")

    ensure_dataset = upload_file = upload_text = parse_status = delete = delete_dataset = _off

    def search(self, project, query: str, top_k: int) -> list[Chunk]:
        return []
