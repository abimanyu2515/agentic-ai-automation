"""KnowledgeService: a project's knowledge base (spec 0006).

Upload saves the real file under the project's own folder and queues it for
indexing. Search reads only the project's own dataset and keeps only chunks
that map to a local record of the same project.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import shutil
import unicodedata
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from backend.config.settings import get_settings
from backend.models.document import Document
from backend.services.knowledge_indexer import DISABLED_ERROR, get_knowledge_indexer, release_repository_session
from backend.services.retrieval import get_retriever

logger = logging.getLogger(__name__)

SOURCE_TYPES = ("document", "jira_story", "test_case", "execution_failure", "defect", "locator")
SEARCH_TOP_K_DEFAULT = 5
SEARCH_TOP_K_MAX = 10
NAME_MAX = 120
READ_CHUNK = 1024 * 1024
NOT_SET_UP = "the knowledge base is not set up for this project"

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_UNSAFE = re.compile(r'[<>:"|?*\x00-\x1f]')


class KnowledgeUploadError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class KnowledgeNotReady(Exception):
    """RAG is disabled, or the project has no dataset yet."""


def sanitize_filename(name: str) -> str:
    """AC-1: no path separators or `..`, no Windows reserved names, no
    trailing dots or spaces, at most 120 characters."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").split("/")[-1]
    name = _UNSAFE.sub("_", name)
    while ".." in name:
        name = name.replace("..", ".")
    name = name.strip().rstrip(". ").lstrip(". ")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = stem.rstrip(". ") or "file"
    if stem.split(".")[0].upper() in _RESERVED:
        stem = f"_{stem}"
    suffix = f".{ext}" if ext else ""
    stem = stem[: max(1, NAME_MAX - len(suffix))].rstrip(". ") or "file"
    return f"{stem}{suffix}"[:NAME_MAX]


def file_extension(name: str) -> str:
    return Path(name or "").suffix.lower().lstrip(".")


class KnowledgeService:
    def __init__(self, repository=None, retriever=None, indexer=None, settings=None):
        self._repository = repository
        self._retriever = retriever
        self._indexer = indexer
        self._settings = settings

    @property
    def repo(self):
        if self._repository is not None:
            return self._repository
        from backend.repository.project_repository import get_project_repository

        return get_project_repository()

    @property
    def retriever(self):
        return self._retriever if self._retriever is not None else get_retriever()

    @property
    def indexer(self):
        return self._indexer if self._indexer is not None else get_knowledge_indexer()

    @property
    def settings(self):
        return self._settings or get_settings()

    # ------------------------------------------------------------ documents

    def list_documents(self, project_id: UUID) -> list[Document]:
        return self.repo.get_documents(project_id)

    def delete(self, project, document_id: UUID) -> bool:
        """AC-5: delete the record and the local file, then queue deletion of
        the remote document from this project's dataset only. Returns False
        when the document is not in this project. The caller has already
        checked project access and permission."""
        dataset_id = getattr(self.repo.get_project(project.id) or project, "rag_dataset_id", None)
        indexer = self.indexer
        with indexer.write_guard:
            document = self.repo.get_document(project.id, document_id)
            if document is None:
                return False
            self.repo.delete_document(project.id, document_id)
        self._remove_document_folder(project.id, document)
        if document.remote_doc_id and dataset_id:
            indexer.enqueue_remote_delete(project.id, dataset_id, [document.remote_doc_id], reason="document_deleted")
        logger.info(
            "Knowledge document deleted: project=%s document=%s remote=%s",
            project.id, document_id, document.remote_doc_id,
        )
        return True

    def _project_folder(self, project_id: UUID) -> Path:
        return Path(self.settings.rag.storage_root) / str(project_id)

    def _remove_document_folder(self, project_id: UUID, document: Document) -> None:
        """Remove <root>/<project>/documents/<document_id>/ and nothing else.
        Documents from before spec 0006 carry a client supplied storage_path,
        so a path outside that folder is never touched."""
        expected = (self._project_folder(project_id) / "documents" / str(document.id)).resolve()
        folder = Path(document.storage_path).parent.resolve() if document.storage_path else None
        if folder != expected:
            return
        try:
            shutil.rmtree(folder)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Could not remove the file of deleted document %s: %s", document.id, exc)

    # ------------------------------------------------------------ status

    def status(self, project) -> dict:
        """AC-7: whether RAG is on, the dataset, and counts by status."""
        project = self.repo.get_project(project.id) or project
        documents = self.repo.get_documents(project.id)
        counts: dict[str, int] = {}
        for document in documents:
            counts[document.embedding_status] = counts.get(document.embedding_status, 0) + 1
        indexed_times = [d.uploaded_at for d in documents if d.embedding_status == "completed"]
        entries: dict[str, dict[str, int]] = {}
        for entry in self.repo.list_knowledge_entries(project.id):
            by_status = entries.setdefault(entry.source_type, {})
            by_status[entry.status] = by_status.get(entry.status, 0) + 1
            indexed_times.append(entry.indexed_at)
        return {
            "enabled": self.retriever.is_active(),
            "dataset_id": getattr(project, "rag_dataset_id", None),
            "documents": counts,
            "entries": entries,
            "last_indexed_at": max(indexed_times) if indexed_times else None,
            "pending_deletes": self._pending_deletes(project.id),
        }

    def _pending_deletes(self, project_id: UUID) -> dict:
        """Spec 0007, AC-11: this project's RAGFlow deletes still outstanding."""
        try:
            rows = self.repo.list_remote_deletes(project_id)
        finally:
            release_repository_session(self.repo)
        attempted = [row for row in rows if row.last_attempt_at is not None]
        latest = max(attempted, key=lambda row: row.last_attempt_at) if attempted else None
        return {
            "count": len(rows),
            "oldest_created_at": min((row.created_at for row in rows), default=None),
            "last_error": latest.last_error if latest is not None else None,
        }

    # ------------------------------------------------------------ lifecycle

    def project_deleted(self, project) -> None:
        """AC-10: after a project is soft deleted, remove its folder now and
        queue a best effort delete of its dataset. `project` is the copy read
        before the delete. Never raises."""
        try:
            shutil.rmtree(self._project_folder(project.id), ignore_errors=True)
            dataset_id = getattr(project, "rag_dataset_id", None)
            if dataset_id:
                self.indexer.enqueue_dataset_delete(project.id, dataset_id, reason="project_deleted")
            logger.info("Knowledge cleanup queued for deleted project %s (dataset=%s)", project.id, dataset_id)
        except Exception:
            logger.exception("Knowledge cleanup failed for deleted project %s", project.id)

    def on_startup(self) -> None:
        """Run the one time old document migration (AC-9, JSON store), start
        the indexer, and queue documents a restart left behind (AC-4)."""
        try:
            marked = self.repo.run_knowledge_migration_0006()
            if marked:
                logger.info("Knowledge migration 0006 marked %s old document(s) failed", marked)
        except Exception:
            logger.exception("Knowledge migration 0006 failed")
        indexer = self.indexer
        indexer.start()
        try:
            indexer.sweep()
        except Exception:
            logger.exception("Knowledge startup sweep failed")

    def upload(self, project, stream: BinaryIO, original_filename: str, content_type: str | None) -> Document:
        """Save the file, create the Document, queue indexing (AC-1, AC-8).
        Raises KnowledgeUploadError with 413, 415, or 422."""
        rag = self.settings.rag
        allowed = {t.lower().lstrip(".") for t in rag.upload_types}
        extension = file_extension(original_filename)
        if extension not in allowed:
            raise KnowledgeUploadError(415, f"File type not allowed. Upload one of: {', '.join(sorted(allowed))}.")

        filename = sanitize_filename(original_filename)
        document_id = uuid4()
        folder = Path(rag.storage_root) / str(project.id) / "documents" / str(document_id)
        path = folder / filename
        max_bytes = rag.upload_max_mb * 1024 * 1024

        folder.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with open(path, "wb") as out:
                while chunk := stream.read(READ_CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise KnowledgeUploadError(413, f"File is larger than {rag.upload_max_mb} MB.")
                    out.write(chunk)
            if size == 0:
                raise KnowledgeUploadError(422, "File is empty.")
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

        # The browser's content type is only a hint (AC-1); the extension decides.
        mime_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        document = Document(
            id=document_id,
            project_id=project.id,
            filename=filename,
            original_filename=original_filename or filename,
            mime_type=mime_type,
            size=size,
            storage_path=str(path),
            embedding_status="pending",
        )
        active = self.retriever.is_active()
        if not active:
            document.error = DISABLED_ERROR
        self.repo.save_document(document)
        if active:
            self.indexer.enqueue_document(project.id, document.id)
        logger.info(
            "Knowledge document uploaded: project=%s document=%s size=%s queued=%s",
            project.id, document.id, size, active,
        )
        return document

    # ------------------------------------------------------------ search

    def search(self, project, query: str, top_k: int = SEARCH_TOP_K_DEFAULT, source_types=None) -> list[dict]:
        """Search the project's own dataset (spec 0006 search mapping):
        fetch top_k x 3, keep chunks that map to a local record of this
        project, filter by source type, keep the best top_k."""
        query = (query or "").strip()
        if not query:
            raise ValueError("query is required")
        wanted = set(source_types or [])
        unknown = wanted - set(SOURCE_TYPES)
        if unknown:
            raise ValueError(f"unknown source_types: {', '.join(sorted(unknown))}")
        top_k = max(1, min(int(top_k or SEARCH_TOP_K_DEFAULT), SEARCH_TOP_K_MAX))

        retriever = self.retriever
        # Read the project again: a caller's copy may predate its dataset.
        project = self.repo.get_project(project.id) or project
        if not retriever.is_active() or not getattr(project, "rag_dataset_id", None):
            raise KnowledgeNotReady(NOT_SET_UP)

        chunks = retriever.search(project, query, top_k * 3)
        sources = self._sources(project, [chunk.remote_doc_id for chunk in chunks])
        items = []
        for chunk in chunks:
            source = sources.get(chunk.remote_doc_id)
            if source is None:
                continue
            source_type, source_ref, title = source
            if wanted and source_type not in wanted:
                continue
            items.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text,
                    "score": round(float(chunk.score), 4),
                    "source_type": source_type,
                    "source_ref": source_ref,
                    "title": title,
                }
            )
        items.sort(key=lambda item: item["score"], reverse=True)
        # The same file uploaded twice, or overlapping chunks, return the same text;
        # keep only its best scoring copy so repeats do not crowd out other passages.
        seen: set[str] = set()
        unique = []
        for item in items:
            key = " ".join((item["text"] or "").split()).casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique[:top_k]

    def _sources(self, project, remote_ids: list[str]) -> dict[str, tuple[str, str, str]]:
        """remote_doc_id -> (source_type, source_ref, title), for this project only:
        uploaded documents and knowledge entries (test cases, stories, and the rest)."""
        unique = list(dict.fromkeys(r for r in remote_ids if r))
        mapping: dict[str, tuple[str, str, str]] = {}
        if not unique:
            return mapping
        for document in self.repo.find_documents_by_remote_ids(project.id, unique):
            mapping[document.remote_doc_id] = ("document", str(document.id), document.original_filename)
        for entry in self.repo.find_knowledge_entries_by_remote_ids(project.id, unique):
            mapping.setdefault(entry.remote_doc_id, (entry.source_type, entry.source_ref, entry.title))
        return mapping


def knowledge_settings_overrides(rag: dict) -> dict:
    """The `rag` section of a settings save, made safe to write to override.yaml.

    Spec 0006: there is no global dataset, and the RAGFlow API key is write
    only and lives in .env. The key is never written to override.yaml (that
    file is not git ignored). An empty key keeps the current one; a new key
    takes effect for the running server through its environment, and you keep
    it after a restart by setting RAGFLOW_API_KEY in .env.
    """
    rag = dict(rag)
    rag.pop("ragflow_dataset_id", None)
    rag.pop("ragflow_api_key_set", None)
    key = rag.pop("ragflow_api_key", None)
    if isinstance(key, str) and key.strip():
        import os

        os.environ["RAGFLOW_API_KEY"] = key.strip()
        logger.info("RAGFlow API key updated for this server process")
    return rag
