"""KnowledgeIndexer: one background worker that indexes project knowledge (spec 0006).

Queue items carry IDs only: (kind, project_id, ref). When a job runs, the
worker reads the project and the document again, so it always works on
current data. Waits (parse polling and retries) are scheduled as timed jobs,
never slept, so one slow document does not block the queue. A clock is
injected for tests.

It covers dataset creation under a per project lock, file upload, parse
polling, status updates, retries with backoff (AC-4), remote document and
dataset deletes (AC-5, AC-10), the startup sweep (AC-4), and knowledge entry
jobs for test cases, Jira stories, failed executions, defects read from Jira,
and object repository pages, with the hash skip and replacement (AC-14,
AC-14a, AC-15).

Every remote delete is first saved as a `KnowledgeRemoteDelete` row (spec
0007). The row stays until RAGFlow confirms the delete or says the target is
already gone. Rows are replayed by the startup sweep and by a resweep job
every `rag.delete_resweep_minutes`, so an outage or a restart never leaves
project data behind in RAGFlow.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import logging
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from backend.config.settings import get_settings
from backend.models.knowledge import (
    ENTRY_FAILED,
    ENTRY_INDEXED,
    REMOTE_DELETE_DATASET,
    REMOTE_DELETE_DOCUMENT,
    REMOTE_DELETE_ERROR_CHARS,
    KnowledgeEntry,
    KnowledgeRemoteDelete,
)
from backend.services.retrieval import (
    PARSE_DONE,
    PARSE_FAILED,
    RemoteNotFound,
    RetrieverError,
    get_retriever,
)

logger = logging.getLogger(__name__)

DISABLED_ERROR = "knowledge base is disabled"
FILE_NOT_FOUND = "file not found"
PARSE_TIMED_OUT = "parsing timed out"

JOB_DOCUMENT = "document"
JOB_POLL = "poll"
JOB_REMOTE_DELETE = "remote_delete"
JOB_DATASET_DELETE = "dataset_delete"
JOB_SOURCE = "source"
JOB_RESWEEP = "resweep"
DOCUMENT_JOBS = {JOB_DOCUMENT, JOB_POLL}
DELETE_JOBS = {JOB_REMOTE_DELETE, JOB_DATASET_DELETE}

# Outcomes of one pending delete attempt (spec 0007). The reindex script counts them.
DELETE_DONE = "deleted"  # RAGFlow confirmed, or said it was already gone
DELETE_DROPPED = "dropped"  # a dataset row covers this document row
DELETE_GONE = "gone"  # another process already finished the row
DELETE_RETRYING = "retrying"  # a short retry is scheduled
DELETE_PENDING = "pending"  # the row stays for the next sweep or resweep
DELETE_FINISHED = {DELETE_DONE, DELETE_DROPPED, DELETE_GONE}


@dataclass(order=True)
class _Job:
    due: float
    seq: int
    kind: str = field(compare=False)
    project_id: UUID = field(compare=False)
    ref: UUID | None = field(compare=False)
    data: dict = field(compare=False, default_factory=dict)
    # Set by _retry when it schedules this job again, so _run keeps its row queued.
    requeued: bool = field(compare=False, default=False)


def _dataset_ref(project_id: UUID, dataset_id: str):
    """The project as it was when its dataset ID was captured (before a delete)."""
    return SimpleNamespace(id=project_id, rag_dataset_id=dataset_id)


def release_repository_session(repo) -> None:
    """Never hold a PostgreSQL session across a wait (spec 0006)."""
    module = sys.modules.get("backend.repository.postgres_project_repository")
    if module is not None and isinstance(repo, module.PostgresProjectRepository):
        module.db_session.remove()


class KnowledgeIndexer:
    def __init__(self, retriever=None, repository=None, settings=None, clock=time.monotonic, source_reader=None, jira_service=None):
        self._retriever = retriever
        # Defects are read from Jira (AC-14a). Tests inject a fake, so they never call Jira.
        self._jira_service = jira_service
        self._repository = repository
        self._settings = settings
        self._source_reader = source_reader
        self.clock = clock
        # Outcome counts (indexed, skipped, failed, removed); the reindex script reports them.
        self.stats: Counter = Counter()
        self._heap: list[_Job] = []
        self._seq = itertools.count()
        self._cond = threading.Condition()
        self._worker: threading.Thread | None = None
        self._stopping = False
        self._project_locks: dict[UUID, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # Held around every read then write of a document, by the indexer and
        # by KnowledgeService.delete, so an indexer save can never bring back
        # a document that was deleted in between (AC-5).
        self.write_guard = threading.RLock()
        # Pending delete row IDs that have a job in the heap (spec 0007, AC-7). Guarded by _cond.
        self._queued_deletes: set[UUID] = set()

    # ------------------------------------------------------------ dependencies

    @property
    def retriever(self):
        return self._retriever if self._retriever is not None else get_retriever()

    @property
    def repo(self):
        if self._repository is not None:
            return self._repository
        from backend.repository.project_repository import get_project_repository

        return get_project_repository()

    @property
    def settings(self):
        return self._settings or get_settings()

    def _release(self) -> None:
        """Never hold a PostgreSQL session across a wait (spec 0006)."""
        release_repository_session(self.repo)

    # ------------------------------------------------------------ queue

    def enqueue_document(self, project_id: UUID, document_id: UUID) -> None:
        self._schedule(JOB_DOCUMENT, project_id, document_id, delay=0)

    def enqueue_remote_delete(self, project_id: UUID, dataset_id: str, remote_ids: list[str], *, reason: str) -> None:
        """Delete remote documents from the dataset captured before the local
        delete (spec 0006 AC-5). Call it only after the local change succeeded:
        each ID is saved as a pending delete row first (spec 0007, AC-1)."""
        if not dataset_id:
            return
        for remote_id in dict.fromkeys(r for r in remote_ids if r):
            row = KnowledgeRemoteDelete(
                project_id=project_id,
                kind=REMOTE_DELETE_DOCUMENT,
                dataset_id=dataset_id,
                remote_doc_id=remote_id,
                reason=reason,
            )
            self._queue_delete_row(row)

    def enqueue_dataset_delete(self, project_id: UUID, dataset_id: str, *, reason: str) -> None:
        """Delete a whole dataset (spec 0006 AC-10), saved as a pending delete row first (spec 0007)."""
        if dataset_id:
            row = KnowledgeRemoteDelete(project_id=project_id, kind=REMOTE_DELETE_DATASET, dataset_id=dataset_id, reason=reason)
            self._queue_delete_row(row)

    def _queue_delete_row(self, row: KnowledgeRemoteDelete) -> None:
        try:
            saved = self.repo.add_remote_delete(row)
        except Exception:
            # AC-3: the local delete already succeeded, so never fail it. Run
            # the delete in memory only, as before spec 0007.
            logger.error(
                "Could not save a pending RAGFlow delete, running it in memory only: project=%s dataset=%s remote=%s reason=%s",
                row.project_id, row.dataset_id, row.remote_doc_id, row.reason, exc_info=True,
            )
            self._release()
            self._schedule(self._delete_job_kind(row), row.project_id, None, delay=0, **self._delete_job_data(row, row_id=None))
            return
        self._release()
        if saved is None:
            logger.debug(
                "Pending RAGFlow delete covered by its dataset delete: project=%s dataset=%s remote=%s",
                row.project_id, row.dataset_id, row.remote_doc_id,
            )
            return
        self._schedule_delete_row(saved)

    @staticmethod
    def _delete_job_kind(row: KnowledgeRemoteDelete) -> str:
        return JOB_DATASET_DELETE if row.kind == REMOTE_DELETE_DATASET else JOB_REMOTE_DELETE

    @staticmethod
    def _delete_job_data(row: KnowledgeRemoteDelete, row_id) -> dict:
        data = {"dataset_id": row.dataset_id, "row_id": row_id, "reason": row.reason}
        if row.kind != REMOTE_DELETE_DATASET:
            data["remote_ids"] = [row.remote_doc_id]
        return data

    def _schedule_delete_row(self, row: KnowledgeRemoteDelete) -> bool:
        """Queue a job for a saved row unless it already has one (AC-7). Returns True when queued."""
        with self._cond:
            if row.id in self._queued_deletes:
                return False
            self._queued_deletes.add(row.id)
            self._schedule(self._delete_job_kind(row), row.project_id, None, delay=0, **self._delete_job_data(row, row_id=row.id))
        return True

    def _queue_saved_deletes(self) -> int:
        """Queue every saved row that has no job yet (the sweep and the resweep)."""
        try:
            rows = self.repo.list_remote_deletes(None)
        finally:
            self._release()
        return sum(1 for row in rows if self._schedule_delete_row(row))

    def enqueue_source(self, project_id: UUID, source_type: str, ref) -> None:
        """Index a project source again (AC-15). The worker reads the source
        when the job runs: changed content replaces the remote document, the
        same content is skipped, and a source that is gone is removed."""
        if not ref:
            return
        self._schedule(JOB_SOURCE, project_id, None, delay=0, source_type=source_type, source_ref=str(ref), remove=False)

    def enqueue_source_delete(self, project_id: UUID, source_type: str, ref) -> None:
        """Remove a deleted source's entry and its remote document (AC-14)."""
        if not ref:
            return
        self._schedule(JOB_SOURCE, project_id, None, delay=0, source_type=source_type, source_ref=str(ref), remove=True)

    def _schedule(self, kind: str, project_id: UUID, ref: UUID | None, delay: float, **data) -> None:
        job = _Job(self.clock() + delay, next(self._seq), kind, project_id, ref, data)
        with self._cond:
            heapq.heappush(self._heap, job)
            self._cond.notify()

    def pending(self) -> int:
        with self._cond:
            return len(self._heap)

    def seconds_until_next(self) -> float | None:
        """Seconds until the next job is due (0 when one is due now), or None when empty."""
        with self._cond:
            if not self._heap:
                return None
            return max(0.0, self._heap[0].due - self.clock())

    def drain(self, sleeper=time.sleep) -> None:
        """Run jobs on this thread until the queue is empty, waiting for
        scheduled polls and retries. The reindex script uses it."""
        while (wait := self.seconds_until_next()) is not None:
            if wait > 0:
                sleeper(wait)
            self.run_pending()

    def _pop_due(self) -> _Job | None:
        with self._cond:
            if self._heap and self._heap[0].due <= self.clock():
                return heapq.heappop(self._heap)
            return None

    def run_pending(self) -> int:
        """Run every job that is due now, including ones they schedule for now.
        The worker uses it; tests call it directly with a fake clock."""
        count = 0
        while (job := self._pop_due()) is not None:
            self._run(job)
            count += 1
        return count

    def start(self) -> None:
        with self._cond:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stopping = False
            # The startup sweep covers now, so the first resweep runs one interval out (AC-7).
            if not any(job.kind == JOB_RESWEEP for job in self._heap):
                self._schedule(JOB_RESWEEP, None, None, delay=self._resweep_seconds())
            self._worker = threading.Thread(target=self._loop, name="knowledge-indexer", daemon=True)
            self._worker.start()

    def stop(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._worker is not None:
            self._worker.join(timeout)

    def _loop(self) -> None:
        while True:
            with self._cond:
                if self._stopping:
                    return
                if not self._heap:
                    self._cond.wait(timeout=5)
                    continue
                wait = self._heap[0].due - self.clock()
                if wait > 0:
                    self._cond.wait(timeout=min(wait, 5))
                    continue
            self.run_pending()

    def _run(self, job: _Job) -> None:
        try:
            if job.kind == JOB_DOCUMENT:
                self._index_document(job)
            elif job.kind == JOB_POLL:
                self._poll_document(job)
            elif job.kind == JOB_REMOTE_DELETE:
                self._delete_remote(job)
            elif job.kind == JOB_DATASET_DELETE:
                self._delete_dataset(job)
            elif job.kind == JOB_SOURCE:
                self._index_source(job)
            elif job.kind == JOB_RESWEEP:
                self._resweep()
            else:
                logger.error("Knowledge indexer: unknown job kind %s", job.kind)
        except Exception:
            logger.exception("Knowledge indexer job failed: kind=%s project=%s ref=%s data=%s", job.kind, job.project_id, job.ref, job.data)
            self.stats["failed"] += 1
            if job.kind in DOCUMENT_JOBS:
                try:
                    self._fail(job.project_id, job.ref, "indexing failed unexpectedly")
                except Exception:
                    logger.exception("Knowledge indexer could not record the failure")
        finally:
            row_id = job.data.get("row_id") if job.kind in DELETE_JOBS else None
            if row_id is not None and not job.requeued:
                # AC-7: whatever the outcome, the row may be queued again later.
                with self._cond:
                    self._queued_deletes.discard(row_id)
            self._release()

    def _retry(self, job: _Job, exc: RetrieverError) -> bool:
        """AC-4: retry a transient RAGFlow failure after each configured delay.
        Returns False when the error is not transient or the retries ran out."""
        delays = list(self.settings.rag.index_retry_delays)
        attempt = int(job.data.get("attempt", 0))
        if not getattr(exc, "transient", False) or attempt >= len(delays):
            return False
        logger.warning(
            "Knowledge indexer retrying in %ss (retry %s of %s): kind=%s project=%s ref=%s error=%s",
            delays[attempt], attempt + 1, len(delays), job.kind, job.project_id, job.ref, exc,
        )
        self._schedule(job.kind, job.project_id, job.ref, delay=delays[attempt], **{**job.data, "attempt": attempt + 1})
        job.requeued = True
        return True

    # ------------------------------------------------------------ startup sweep

    def sweep(self) -> int:
        """AC-4: queue every document still pending or processing, so a restart
        does not leave documents stuck, and every pending delete row (spec 0007,
        AC-6). Returns both counts added together. Does nothing while RAG is
        disabled (AC-8)."""
        if not self.retriever.is_active():
            logger.info("Knowledge sweep skipped: the knowledge base is disabled")
            return 0
        queued = 0
        try:
            for project in self.repo.list_projects():
                for document in self.repo.get_documents(project.id):
                    if document.embedding_status == "pending":
                        self.enqueue_document(project.id, document.id)
                    elif document.embedding_status == "processing":
                        if document.remote_doc_id:
                            # Already uploaded: only parsing is left to watch.
                            self._schedule(JOB_POLL, project.id, document.id, delay=0, started_at=self.clock())
                        else:
                            self.enqueue_document(project.id, document.id)
                    else:
                        continue
                    queued += 1
        finally:
            self._release()
        deletes = self._queue_saved_deletes()
        logger.info("Knowledge sweep queued %s document(s) and %s pending delete(s)", queued, deletes)
        return queued + deletes

    def _resweep_seconds(self) -> float:
        return float(self.settings.rag.delete_resweep_minutes) * 60

    def _resweep(self) -> None:
        """AC-7, AC-8: queue every pending delete row without a job, then
        schedule the next resweep, even when this one fails."""
        try:
            if self.retriever.is_active():
                queued = self._queue_saved_deletes()
                if queued:
                    logger.info("Knowledge resweep queued %s pending delete(s)", queued)
            else:
                logger.debug("Knowledge resweep skipped: the knowledge base is disabled")
        finally:
            self._schedule(JOB_RESWEEP, None, None, delay=self._resweep_seconds())

    # ------------------------------------------------------------ documents

    def _update(self, project_id: UUID, document_id: UUID, **fields):
        """Read the document again and save the fields, unless it was deleted.
        Returns the saved document, or None when it no longer exists."""
        with self.write_guard:
            current = self.repo.get_document(project_id, document_id)
            if current is not None:
                for name, value in fields.items():
                    setattr(current, name, value)
                self.repo.save_document(current)
        self._release()
        return current

    def _fail(self, project_id: UUID, document_id: UUID, message: str) -> None:
        """Mark the document failed. When it has a remote copy, clear the ID in
        the same write and queue a `parse_failed` delete, so a retry uploads
        one fresh copy instead of a second one (spec 0007, AC-9)."""
        old_remote_id = None
        with self.write_guard:
            current = self.repo.get_document(project_id, document_id)
            if current is not None:
                old_remote_id = current.remote_doc_id
                current.embedding_status = "failed"
                current.error = message
                current.remote_doc_id = None
                self.repo.save_document(current)
        self._release()
        if current is None:
            # Already deleted: its `document_deleted` row covers the remote copy.
            return
        self.stats["failed"] += 1
        logger.warning("Knowledge document failed: project=%s document=%s error=%s", project_id, document_id, message)
        if old_remote_id:
            project = self.repo.get_project(project_id)
            self._release()
            dataset_id = getattr(project, "rag_dataset_id", None)
            self.enqueue_remote_delete(project_id, dataset_id, [old_remote_id], reason="parse_failed")

    def _index_document(self, job: _Job) -> None:
        repo = self.repo
        project = repo.get_project(job.project_id)
        document = repo.get_document(job.project_id, job.ref)
        self._release()
        if project is None or document is None:
            logger.info("Knowledge indexer skipped a missing document: project=%s document=%s", job.project_id, job.ref)
            return
        if document.embedding_status not in {"pending", "processing"}:
            return

        retriever = self.retriever
        if not retriever.is_active():
            # AC-8: stays pending until RAG is enabled.
            self._update(job.project_id, job.ref, error=DISABLED_ERROR)
            return

        path = Path(document.storage_path)
        if not path.is_file():
            # Not retried: the file will not come back on its own.
            self._fail(job.project_id, job.ref, FILE_NOT_FOUND)
            return

        logger.info("Knowledge indexing started: project=%s document=%s attempt=%s", job.project_id, job.ref, job.data.get("attempt", 0))
        if self._update(job.project_id, job.ref, embedding_status="processing", error=None) is None:
            return

        try:
            project = self._ensure_dataset(project)
            result = retriever.upload_file(project, path, document.filename)
        except RetrieverError as exc:
            if not self._retry(job, exc):
                self._fail(job.project_id, job.ref, str(exc))
            return

        saved = self._update(job.project_id, job.ref, remote_doc_id=result.remote_doc_id, embedding_status="processing")
        if saved is None:
            # Deleted while it was uploading: remove the new remote copy instead of keeping it (AC-5).
            logger.info("Knowledge document deleted during upload, removing its remote copy: project=%s document=%s", job.project_id, job.ref)
            self.enqueue_remote_delete(job.project_id, project.rag_dataset_id, [result.remote_doc_id], reason="upload_raced_delete")
            return
        self._schedule(
            JOB_POLL,
            job.project_id,
            job.ref,
            delay=self.settings.rag.parse_poll_seconds,
            started_at=self.clock(),
        )

    def _poll_document(self, job: _Job) -> None:
        repo = self.repo
        project = repo.get_project(job.project_id)
        document = repo.get_document(job.project_id, job.ref)
        self._release()
        if project is None or document is None or not document.remote_doc_id:
            return
        if document.embedding_status != "processing":
            return
        try:
            status = self.retriever.parse_status(project, document.remote_doc_id)
        except RetrieverError as exc:
            if not self._retry(job, exc):
                self._fail(job.project_id, job.ref, str(exc))
            return

        if status.state == PARSE_DONE:
            if self._update(job.project_id, job.ref, embedding_status="completed", error=None) is not None:
                self.stats["indexed"] += 1
                logger.info("Knowledge indexing completed: project=%s document=%s", job.project_id, job.ref)
        elif status.state == PARSE_FAILED:
            self._fail(job.project_id, job.ref, status.message or "parsing failed")
        elif self.clock() - job.data["started_at"] >= self.settings.rag.parse_timeout_seconds:
            self._fail(job.project_id, job.ref, PARSE_TIMED_OUT)
        else:
            # A successful poll resets the retry count.
            self._schedule(
                JOB_POLL,
                job.project_id,
                job.ref,
                delay=self.settings.rag.parse_poll_seconds,
                started_at=job.data["started_at"],
            )

    # ------------------------------------------------------------ knowledge entries

    def _read_source(self, project, source_type: str, ref: str):
        if self._source_reader is not None:
            return self._source_reader(self.repo, project, source_type, ref)
        from backend.services.knowledge_sources import read_source

        jira_service = None
        if source_type == "defect":
            if self._jira_service is None:
                from backend.services.jira_service import JiraService

                self._jira_service = JiraService()
            jira_service = self._jira_service
        return read_source(self.repo, project, source_type, ref, jira_service=jira_service)

    def _index_source(self, job: _Job) -> None:
        source_type, ref = job.data["source_type"], job.data["source_ref"]
        repo = self.repo
        project = repo.get_project(job.project_id)
        self._release()
        if project is None:
            logger.info("Knowledge indexer skipped a source of a missing project: project=%s %s:%s", job.project_id, source_type, ref)
            return
        retriever = self.retriever
        if not retriever.is_active():
            # The reindex script picks these up once RAG is enabled (AC-8, AC-16).
            self.stats["skipped"] += 1
            return

        from backend.services.knowledge_sources import SourceUnavailable

        try:
            source = None if job.data.get("remove") else self._read_source(project, source_type, ref)
        except SourceUnavailable as exc:
            self._release()
            self._source_unavailable(job, project, source_type, ref, exc)
            return
        self._release()
        if source is None:
            self._remove_entry(project, source_type, ref)
            return

        content_hash = hashlib.sha256(source.text.encode("utf-8")).hexdigest()
        existing = repo.get_knowledge_entry(project.id, source_type, ref)
        self._release()
        if (
            existing is not None
            and existing.content_hash == content_hash
            and existing.status == ENTRY_INDEXED
            and existing.remote_doc_id
        ):
            self.stats["skipped"] += 1
            logger.info("Knowledge entry unchanged: project=%s %s:%s", project.id, source_type, ref)
            return

        try:
            project = self._ensure_dataset(project)
            result = retriever.upload_text(project, source.text, f"{source_type}__{ref}.txt")
        except RetrieverError as exc:
            if not self._retry(job, exc):
                self._save_entry(project, source_type, ref, source.title, content_hash, existing, status=ENTRY_FAILED, error=str(exc))
                self.stats["failed"] += 1
                logger.warning("Knowledge entry failed: project=%s %s:%s error=%s", project.id, source_type, ref, exc)
            return

        self._save_entry(project, source_type, ref, source.title, content_hash, existing, remote_doc_id=result.remote_doc_id)
        if existing is not None and existing.remote_doc_id and existing.remote_doc_id != result.remote_doc_id:
            # Changed content replaces the old remote document (AC-14).
            self.enqueue_remote_delete(project.id, project.rag_dataset_id, [existing.remote_doc_id], reason="entry_replaced")
        self.stats["indexed"] += 1
        logger.info("Knowledge entry indexed: project=%s %s:%s remote=%s", project.id, source_type, ref, result.remote_doc_id)

    def _source_unavailable(self, job: _Job, project, source_type: str, ref: str, exc) -> None:
        """AC-14a: retry a transient read on the retry delays, then mark the
        entry failed. A non transient one (Jira in mock mode) writes nothing.
        Either way an existing entry is never removed."""
        if not exc.transient:
            self.stats["skipped"] += 1
            logger.info("Knowledge source skipped: project=%s %s:%s reason=%s", project.id, source_type, ref, exc)
            return
        if self._retry(job, exc):
            return
        existing = self.repo.get_knowledge_entry(project.id, source_type, ref)
        self._release()
        self._save_entry(
            project, source_type, ref,
            existing.title if existing else ref,
            existing.content_hash if existing else "",
            existing, status=ENTRY_FAILED, error=str(exc),
        )
        self.stats["failed"] += 1
        logger.warning("Knowledge entry failed: project=%s %s:%s error=%s", project.id, source_type, ref, exc)

    def _save_entry(self, project, source_type, ref, title, content_hash, existing, status=ENTRY_INDEXED, error=None, remote_doc_id=None):
        fields = dict(
            project_id=project.id,
            source_type=source_type,
            source_ref=ref,
            title=title or f"{source_type} {ref}",
            # A failed upload keeps the old remote document, so a later success can replace it.
            remote_doc_id=remote_doc_id or (existing.remote_doc_id if existing else None),
            content_hash=content_hash,
            status=status,
            error=error,
            indexed_at=datetime.now(timezone.utc),
        )
        if existing is not None:
            fields["id"] = existing.id
        with self.write_guard:
            self.repo.save_knowledge_entry(KnowledgeEntry(**fields))
        self._release()

    def _remove_entry(self, project, source_type: str, ref: str) -> None:
        with self.write_guard:
            existing = self.repo.get_knowledge_entry(project.id, source_type, ref)
            if existing is not None:
                self.repo.delete_knowledge_entry(project.id, source_type, ref)
        self._release()
        if existing is None:
            return
        if existing.remote_doc_id and project.rag_dataset_id:
            self.enqueue_remote_delete(project.id, project.rag_dataset_id, [existing.remote_doc_id], reason="entry_removed")
        self.stats["removed"] += 1
        logger.info("Knowledge entry removed: project=%s %s:%s", project.id, source_type, ref)

    # ------------------------------------------------------------ deletes

    def _delete_remote(self, job: _Job) -> None:
        self._run_delete_row(job)

    def _delete_dataset(self, job: _Job) -> None:
        self._run_delete_row(job)

    def run_delete_row_once(self, row: KnowledgeRemoteDelete) -> str:
        """One attempt at a saved row, with no short retries (the reindex
        script, spec 0007 AC-12). Returns a DELETE_* outcome."""
        job = _Job(self.clock(), next(self._seq), self._delete_job_kind(row), row.project_id, None, self._delete_job_data(row, row_id=row.id))
        try:
            return self._run_delete_row(job, retries=False)
        finally:
            self._release()

    def _run_delete_row(self, job: _Job, retries: bool = True) -> str:
        """Make one RAGFlow call for a pending delete (spec 0007, AC-4, AC-5).

        A row job reads its row again first: a missing row is done, and a
        document row whose dataset has a dataset row is removed without a
        call. Success or "already gone" removes the row. A failure retries on
        the short delays, then records the attempt and keeps the row. A
        fallback job (no row, AC-3) only retries and logs."""
        row_id = job.data.get("row_id")
        is_dataset = job.kind == JOB_DATASET_DELETE
        dataset_id = job.data["dataset_id"]
        remote_ids = job.data.get("remote_ids") or []
        if row_id is not None:
            row = self.repo.get_remote_delete(row_id)
            self._release()
            if row is None:
                logger.debug("Pending RAGFlow delete already finished: row=%s", row_id)
                return DELETE_GONE
            dataset_id = row.dataset_id
            if not is_dataset:
                remote_ids = [row.remote_doc_id]
                covered = self.repo.get_dataset_delete(row.dataset_id) is not None
                self._release()
                if covered:
                    self.repo.remove_remote_delete(row_id)
                    self._release()
                    logger.debug("Pending RAGFlow delete covered by its dataset delete: row=%s", row_id)
                    return DELETE_DROPPED

        retriever = self.retriever
        if not retriever.is_active():
            # AC-8: keep the row untouched until RAG is enabled again.
            return DELETE_PENDING

        target = _dataset_ref(job.project_id, dataset_id)
        what = f"dataset={dataset_id}" if is_dataset else f"dataset={dataset_id} remote={remote_ids}"
        try:
            if is_dataset:
                retriever.delete_dataset(target)
            else:
                retriever.delete(target, remote_ids)
        except RemoteNotFound as exc:
            logger.info("Knowledge remote delete: already gone in RAGFlow: project=%s %s (%s)", job.project_id, what, exc)
        except RetrieverError as exc:
            if retries and self._retry(job, exc):
                return DELETE_RETRYING
            logger.warning(
                "Knowledge remote delete failed, kept for the next resweep: project=%s %s reason=%s error=%s",
                job.project_id, what, job.data.get("reason"), exc,
            )
            if row_id is not None:
                self.repo.record_remote_delete_failure(row_id, str(exc)[:REMOTE_DELETE_ERROR_CHARS], datetime.now(timezone.utc))
                self._release()
            return DELETE_PENDING
        else:
            logger.info("Knowledge remote delete done: project=%s %s", job.project_id, what)
        if row_id is not None:
            self.repo.remove_remote_delete(row_id)
            self._release()
        return DELETE_DONE

    # ------------------------------------------------------------ datasets

    def _project_lock(self, project_id: UUID) -> threading.Lock:
        with self._locks_guard:
            return self._project_locks.setdefault(project_id, threading.Lock())

    def _ensure_dataset(self, project):
        """AC-2: one dataset per project, even with concurrent first indexes."""
        if project.rag_dataset_id:
            return project
        with self._project_lock(project.id):
            fresh = self.repo.get_project(project.id) or project
            self._release()
            if fresh.rag_dataset_id:
                return fresh
            created = self.retriever.ensure_dataset(fresh)
            stored = self.repo.set_project_rag_dataset(project.id, created)
            self._release()
            if stored and stored != created:
                # Another writer got there first: queue ours for deletion, use the saved one (spec 0007, AC-10).
                logger.info("Knowledge dataset race: project=%s keeps %s, deleting the extra %s", project.id, stored, created)
                self.enqueue_dataset_delete(project.id, created, reason="duplicate_dataset")
            return fresh.model_copy(update={"rag_dataset_id": stored or created})


_indexer: KnowledgeIndexer | None = None
_indexer_lock = threading.Lock()


def get_knowledge_indexer() -> KnowledgeIndexer:
    global _indexer
    with _indexer_lock:
        if _indexer is None:
            _indexer = KnowledgeIndexer()
        return _indexer


def queue_knowledge_source(project_id, source_type: str, ref, remove: bool = False) -> None:
    """The indexing hook (AC-15). Call it after a save succeeds. It only
    enqueues, and it never raises, so it cannot slow down or fail the save."""
    if not project_id or not ref:
        return
    try:
        indexer = get_knowledge_indexer()
        if remove:
            indexer.enqueue_source_delete(project_id, source_type, ref)
        else:
            indexer.enqueue_source(project_id, source_type, ref)
    except Exception:
        logger.exception("Could not queue knowledge indexing: project=%s %s:%s", project_id, source_type, ref)


def queue_defect_keys(project_id, keys) -> None:
    """Queue each trimmed, non empty bug key once (AC-14a). The worker checks the
    project's links when the job runs and removes the entry when none is left,
    so a delete hook queues an ordinary job, never a forced removal."""
    from backend.services.knowledge_sources import defect_key

    for key in dict.fromkeys(filter(None, (defect_key(k) for k in keys or []))):
        queue_knowledge_source(project_id, "defect", key)


def queue_test_case_removals(project_id, test_cases) -> None:
    """Remove deleted test cases from the index and recheck their bug keys
    (AC-14a, AC-15). Read the test cases before the delete that removes them,
    for example a scenario delete that soft deletes its children."""
    test_cases = list(test_cases or [])
    for case in test_cases:
        queue_knowledge_source(project_id, "test_case", getattr(case, "id", None), remove=True)
    queue_defect_keys(project_id, [getattr(case, "jira_issue_key", None) for case in test_cases])


def set_knowledge_indexer(indexer: KnowledgeIndexer | None) -> None:
    """Swap the process wide indexer. Tests pass one built with fakes."""
    global _indexer
    with _indexer_lock:
        _indexer = indexer
