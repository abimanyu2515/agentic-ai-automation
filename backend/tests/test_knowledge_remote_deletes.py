"""Pending RAGFlow deletes (spec 0007).

Every remote delete is saved as a KnowledgeRemoteDelete row first, kept until
RAGFlow confirms it or says the target is gone, and replayed by the startup
sweep and the resweep job. JSON store, fakes, and an injected clock: no
RAGFlow, no sleeping. The last tests use the PostgreSQL repository when the
configured database is reachable.
"""

import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.config.settings import get_settings
from backend.models.knowledge import KnowledgeRemoteDelete
from backend.repository.project_repository import JSONProjectRepository
from backend.scripts.reindex_project_knowledge import format_age, pending_deletes
from backend.services.knowledge_indexer import (
    DELETE_DONE,
    JOB_RESWEEP,
    PARSE_TIMED_OUT,
    KnowledgeIndexer,
)
from backend.services.knowledge_service import KnowledgeService
from backend.services.retrieval import PARSE_RUNNING, ParseStatus, RemoteNotFound, RetrieverError
from backend.services.retrieval.fake import InMemoryRetriever
from backend.services.retrieval.ragflow import RAGFlowRetriever


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


class ControlledRetriever(InMemoryRetriever):
    """InMemoryRetriever whose deletes, parse status, and active flag a test controls."""

    def __init__(self):
        super().__init__()
        self.active = True
        self.delete_error: Exception | None = None
        self.delete_calls: list[tuple] = []
        self.default_parse = None
        self.parse_error: Exception | None = None

    def is_active(self) -> bool:
        return self.active

    def delete(self, project, remote_ids):
        self.delete_calls.append(("document", project.rag_dataset_id, list(remote_ids)))
        if self.delete_error is not None:
            raise self.delete_error
        return super().delete(project, remote_ids)

    def delete_dataset(self, project):
        self.delete_calls.append(("dataset", project.rag_dataset_id, None))
        if self.delete_error is not None:
            raise self.delete_error
        return super().delete_dataset(project)

    def parse_status(self, project, remote_id):
        if self.parse_error is not None:
            raise self.parse_error
        if self.default_parse is not None and remote_id not in self.parse_states:
            return self.default_parse
        return super().parse_status(project, remote_id)


OUTAGE = RetrieverError("RAGFlow returned 503", transient=True)


@pytest.fixture
def settings(tmp_path):
    base = get_settings().model_copy(deep=True)
    base.rag.storage_root = str(tmp_path / "projects")
    base.rag.upload_max_mb = 1
    base.rag.parse_poll_seconds = 5
    base.rag.parse_timeout_seconds = 600
    base.rag.index_retry_delays = [10, 60]
    base.rag.delete_resweep_minutes = 30
    return base


@pytest.fixture
def kb(tmp_path, settings):
    repo = JSONProjectRepository(file_path=tmp_path / "store.json")
    retriever = ControlledRetriever()
    clock = FakeClock()

    def build_indexer(repository=repo):
        return KnowledgeIndexer(retriever=retriever, repository=repository, settings=settings, clock=clock)

    indexer = build_indexer()
    service = KnowledgeService(repository=repo, retriever=retriever, indexer=indexer, settings=settings)

    def project(name="A", url=None):
        return repo.create_project(name=name, description="d", target_url=url or f"https://{uuid4().hex[:8]}.example.com")

    def upload(proj, text="Members get a 10 percent discount.", name="spec.txt"):
        return service.upload(proj, io.BytesIO(text.encode()), name, "text/plain")

    def index_all():
        indexer.run_pending()
        clock.advance(settings.rag.parse_poll_seconds)
        indexer.run_pending()

    def run_short_retries(target=None):
        target = target or indexer
        target.run_pending()
        for delay in settings.rag.index_retry_delays:
            clock.advance(delay)
            target.run_pending()

    def indexed_document(proj):
        doc = upload(proj)
        index_all()
        return repo.get_document(proj.id, doc.id)

    return SimpleNamespace(
        repo=repo, retriever=retriever, clock=clock, indexer=indexer, service=service, settings=settings,
        project=project, upload=upload, index_all=index_all, run_short_retries=run_short_retries,
        indexed_document=indexed_document, build_indexer=build_indexer,
    )


def _schedule_resweep(indexer):
    """What start() does, without starting the worker thread."""
    indexer._schedule(JOB_RESWEEP, None, None, delay=indexer._resweep_seconds())


# ------------------------------------------------------------ AC-1, AC-4, AC-6: restart


def test_a_document_delete_survives_a_restart(kb):
    # covers: AC-1, AC-4, AC-6
    project = kb.project()
    doc = kb.indexed_document(project)
    dataset_id = kb.repo.get_project(project.id).rag_dataset_id
    kb.retriever.delete_error = OUTAGE

    assert kb.service.delete(project, doc.id) is True
    [row] = kb.repo.list_remote_deletes(project.id)
    assert (row.kind, row.dataset_id, row.remote_doc_id, row.reason) == (
        "document", dataset_id, doc.remote_doc_id, "document_deleted",
    )
    kb.run_short_retries()
    assert kb.repo.get_remote_delete(row.id).attempts == 1

    # Restart: a new indexer on the same store, RAGFlow back.
    kb.retriever.delete_error = None
    restarted = kb.build_indexer()
    assert restarted.sweep() == 1
    restarted.run_pending()
    assert doc.remote_doc_id not in kb.retriever.datasets[dataset_id]
    assert kb.repo.list_remote_deletes(None) == []


def test_the_row_is_written_only_after_the_local_delete_succeeds(kb, monkeypatch):
    # covers: AC-1
    project = kb.project()
    doc = kb.indexed_document(project)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(kb.repo, "delete_document", broken)
    with pytest.raises(RuntimeError):
        kb.service.delete(project, doc.id)
    kb.indexer.run_pending()
    assert kb.repo.list_remote_deletes(None) == []
    assert kb.retriever.delete_calls == []


# ------------------------------------------------------------ AC-2, AC-5, AC-7: project delete during an outage


def test_project_delete_during_an_outage_is_finished_by_the_resweep(kb):
    # covers: AC-2, AC-5, AC-7
    project = kb.project()
    doc = kb.indexed_document(project)
    dataset_id = kb.repo.get_project(project.id).rag_dataset_id
    kb.retriever.delete_error = OUTAGE

    assert kb.service.delete(project, doc.id) is True  # a document row first
    kb.service.project_deleted(kb.repo.get_project(project.id))  # then the dataset row absorbs it
    rows = kb.repo.list_remote_deletes(project.id)
    assert [(r.kind, r.reason) for r in rows] == [("dataset", "project_deleted")]

    _schedule_resweep(kb.indexer)
    kb.run_short_retries()
    [row] = kb.repo.list_remote_deletes(project.id)
    assert row.attempts == 1 and row.last_error == "RAGFlow returned 503" and row.last_attempt_at is not None
    document_calls = [c for c in kb.retriever.delete_calls if c[0] == "document"]

    kb.retriever.delete_error = None
    kb.clock.advance(30 * 60)
    kb.indexer.run_pending()
    assert dataset_id not in kb.retriever.datasets
    assert kb.repo.list_remote_deletes(None) == []
    # The absorbed document row never reached RAGFlow after the dataset row existed.
    assert [c for c in kb.retriever.delete_calls if c[0] == "document"] == document_calls
    # The resweep scheduled itself again.
    assert any(job.kind == JOB_RESWEEP for job in kb.indexer._heap)


def test_a_document_row_queued_after_its_dataset_row_is_absorbed(kb):
    # covers: AC-2
    project = kb.project()
    kb.indexer.enqueue_dataset_delete(project.id, "ds-x", reason="project_deleted")
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="document_deleted")
    assert [r.kind for r in kb.repo.list_remote_deletes(project.id)] == ["dataset"]


def test_the_worker_drops_a_document_row_whose_dataset_has_a_row(kb):
    # covers: AC-2
    project = kb.project()
    kb.retriever.datasets["ds-x"] = {"doc-1": {"name": "a", "chunks": ["a"]}}
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="document_deleted")
    # A dataset row saved by another process, bypassing the absorb on insert.
    with kb.repo._write_lock:
        raw = kb.repo._read_raw()
        dataset_row = KnowledgeRemoteDelete(project_id=project.id, kind="dataset", dataset_id="ds-x", reason="project_deleted")
        raw["knowledge_remote_deletes"][str(dataset_row.id)] = dataset_row.model_dump(mode="json")
        kb.repo._write_raw(raw)
    kb.indexer.run_pending()
    assert ("document", "ds-x", ["doc-1"]) not in kb.retriever.delete_calls
    assert [r.kind for r in kb.repo.list_remote_deletes(project.id)] == ["dataset"]


# ------------------------------------------------------------ AC-2, AC-7: duplicates


def test_queuing_the_same_document_twice_gives_one_row_and_one_call(kb):
    # covers: AC-2, AC-7
    project = kb.project()
    kb.retriever.datasets["ds-x"] = {"doc-1": {"name": "a", "chunks": ["a"]}}
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="document_deleted")
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="entry_removed")
    assert len(kb.repo.list_remote_deletes(project.id)) == 1
    assert kb.indexer.pending() == 1
    kb.indexer.run_pending()
    assert kb.retriever.delete_calls == [("document", "ds-x", ["doc-1"])]


def test_a_resweep_during_a_short_retry_schedules_nothing_new(kb):
    # covers: AC-7
    project = kb.project()
    kb.retriever.delete_error = OUTAGE
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="document_deleted")
    kb.indexer.run_pending()  # first try fails, a short retry waits 10 seconds
    assert kb.indexer.pending() == 1
    assert kb.indexer._queue_saved_deletes() == 0
    assert kb.indexer.pending() == 1


# ------------------------------------------------------------ AC-4, AC-13: not found


def test_not_found_removes_the_row_with_no_retries(kb):
    # covers: AC-4, AC-13
    project = kb.project()
    kb.retriever.delete_error = RemoteNotFound("gone")
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="document_deleted")
    kb.indexer.run_pending()
    assert kb.repo.list_remote_deletes(None) == []
    assert kb.indexer.pending() == 0
    assert len(kb.retriever.delete_calls) == 1


def test_in_memory_retriever_reports_unknown_targets_as_gone():
    # covers: AC-13
    retriever = InMemoryRetriever()
    target = SimpleNamespace(id=uuid4(), rag_dataset_id="ds-none")
    with pytest.raises(RemoteNotFound):
        retriever.delete(target, ["doc-1"])
    with pytest.raises(RemoteNotFound):
        retriever.delete_dataset(target)


# ------------------------------------------------------------ AC-5: hard error


def test_a_hard_error_records_one_attempt_with_no_short_retries(kb):
    # covers: AC-5
    project = kb.project()
    kb.retriever.delete_error = RetrieverError("RAGFlow: " + "x" * 900, status_code=401)
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="document_deleted")
    kb.indexer.run_pending()
    [row] = kb.repo.list_remote_deletes(project.id)
    assert row.attempts == 1
    assert len(row.last_error) == 500
    assert kb.indexer.pending() == 0
    assert row.id not in kb.indexer._queued_deletes


# ------------------------------------------------------------ AC-7: unexpected exception


def test_an_unexpected_error_frees_the_row_for_the_next_resweep(kb, monkeypatch):
    # covers: AC-7
    project = kb.project()
    kb.retriever.datasets["ds-x"] = {"doc-1": {"name": "a", "chunks": ["a"]}}
    original = kb.repo.get_remote_delete
    calls = {"n": 0}

    def flaky_read(row_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database hiccup")
        return original(row_id)

    monkeypatch.setattr(kb.repo, "get_remote_delete", flaky_read)
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="document_deleted")
    kb.indexer.run_pending()
    [row] = kb.repo.list_remote_deletes(project.id)
    assert row.id not in kb.indexer._queued_deletes

    _schedule_resweep(kb.indexer)
    kb.clock.advance(30 * 60)
    kb.indexer.run_pending()
    assert kb.repo.list_remote_deletes(None) == []
    assert "doc-1" not in kb.retriever.datasets["ds-x"]


def test_start_schedules_one_resweep_an_interval_out(kb):
    # covers: AC-7
    kb.indexer.start()
    kb.indexer.stop()
    kb.indexer.start()
    kb.indexer.stop()
    resweeps = [job for job in kb.indexer._heap if job.kind == JOB_RESWEEP]
    assert len(resweeps) == 1
    assert resweeps[0].due == kb.clock() + 30 * 60


def test_a_resweep_failure_still_reschedules(kb, monkeypatch):
    # covers: AC-7
    def broken(project_id=None):
        raise RuntimeError("database down")

    monkeypatch.setattr(kb.repo, "list_remote_deletes", broken)
    _schedule_resweep(kb.indexer)
    kb.clock.advance(30 * 60)
    kb.indexer.run_pending()
    assert [job.kind for job in kb.indexer._heap] == [JOB_RESWEEP]


# ------------------------------------------------------------ AC-3: saving the row fails


def test_a_failed_row_save_still_deletes_locally_and_runs_in_memory(kb, monkeypatch, caplog):
    # covers: AC-3
    project = kb.project()
    doc = kb.indexed_document(project)
    dataset_id = kb.repo.get_project(project.id).rag_dataset_id

    def broken(row):
        raise RuntimeError("could not save")

    monkeypatch.setattr(kb.repo, "add_remote_delete", broken)
    with caplog.at_level("ERROR"):
        assert kb.service.delete(project, doc.id) is True
    assert "Could not save a pending RAGFlow delete" in caplog.text
    assert kb.repo.get_document(project.id, doc.id) is None
    kb.indexer.run_pending()
    assert doc.remote_doc_id not in kb.retriever.datasets[dataset_id]


# ------------------------------------------------------------ AC-8: disabled


def test_rows_wait_untouched_while_rag_is_disabled(kb):
    # covers: AC-8
    project = kb.project()
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="document_deleted")
    kb.retriever.active = False
    kb.indexer.run_pending()
    assert kb.indexer.sweep() == 0
    _schedule_resweep(kb.indexer)
    kb.clock.advance(30 * 60)
    kb.indexer.run_pending()
    [row] = kb.repo.list_remote_deletes(project.id)
    assert row.attempts == 0 and row.last_error is None
    assert kb.retriever.delete_calls == []
    assert [job.kind for job in kb.indexer._heap] == [JOB_RESWEEP]

    kb.retriever.active = True
    assert kb.indexer.sweep() == 1
    kb.indexer.run_pending()
    assert kb.repo.list_remote_deletes(None) == []


# ------------------------------------------------------------ AC-9: parse failures


def _parse_and_fail(kb, project):
    doc = kb.upload(project)
    kb.indexer.run_pending()  # uploads, schedules the first poll
    return kb.repo.get_document(project.id, doc.id)


def test_a_parse_timeout_clears_the_remote_id_and_queues_its_delete(kb):
    # covers: AC-9
    project = kb.project()
    kb.retriever.default_parse = ParseStatus(PARSE_RUNNING)
    doc = _parse_and_fail(kb, project)
    old_remote = doc.remote_doc_id
    assert old_remote
    kb.clock.advance(kb.settings.rag.parse_timeout_seconds)
    kb.retriever.delete_error = RetrieverError("RAGFlow: Unauthorized", status_code=401)  # keep the row visible
    kb.indexer.run_pending()  # the poll times out
    kb.retriever.delete_error = None

    failed = kb.repo.get_document(project.id, doc.id)
    assert (failed.embedding_status, failed.error, failed.remote_doc_id) == ("failed", PARSE_TIMED_OUT, None)
    [row] = kb.repo.list_remote_deletes(project.id)
    assert (row.remote_doc_id, row.reason) == (old_remote, "parse_failed")

    # A retry uploads one fresh remote document, and the old one is deleted.
    kb.retriever.default_parse = None
    failed.embedding_status, failed.error = "pending", None
    kb.repo.save_document(failed)
    kb.indexer.sweep()  # queues the pending document and replays the parse_failed row
    kb.index_all()
    dataset_id = kb.repo.get_project(project.id).rag_dataset_id
    assert list(kb.retriever.datasets[dataset_id]) == [kb.repo.get_document(project.id, doc.id).remote_doc_id]
    assert kb.repo.list_remote_deletes(None) == []


def test_poll_retries_running_out_also_queue_the_delete(kb):
    # covers: AC-9
    project = kb.project()
    doc = _parse_and_fail(kb, project)
    old_remote = doc.remote_doc_id
    kb.retriever.parse_error = OUTAGE
    kb.clock.advance(kb.settings.rag.parse_poll_seconds)
    kb.run_short_retries()
    failed = kb.repo.get_document(project.id, doc.id)
    assert (failed.embedding_status, failed.remote_doc_id) == ("failed", None)
    assert [(r.remote_doc_id, r.reason) for r in kb.repo.list_remote_deletes(None)] in (
        [(old_remote, "parse_failed")], [],
    )
    assert old_remote not in kb.retriever.datasets[kb.repo.get_project(project.id).rag_dataset_id]


# ------------------------------------------------------------ AC-1: entry and upload race reasons


def test_an_upload_racing_a_delete_queues_its_remote_copy(kb, monkeypatch):
    # covers: AC-1
    project = kb.project()
    doc = kb.upload(project)
    original = kb.retriever.upload_file

    def upload_then_delete(proj, path, name):
        result = original(proj, path, name)
        kb.repo.delete_document(project.id, doc.id)
        return result

    monkeypatch.setattr(kb.retriever, "upload_file", upload_then_delete)
    kb.retriever.delete_error = OUTAGE
    kb.indexer.run_pending()
    assert [r.reason for r in kb.repo.list_remote_deletes(project.id)] == ["upload_raced_delete"]


# ------------------------------------------------------------ AC-11: status


def test_status_reports_pending_deletes(kb):
    # covers: AC-11
    project = kb.project()
    other = kb.project(name="B")
    kb.retriever.delete_error = RetrieverError("RAGFlow: Unauthorized", status_code=401)
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-1"], reason="document_deleted")
    kb.indexer.run_pending()  # one attempt fails and is recorded
    kb.indexer.enqueue_remote_delete(project.id, "ds-x", ["doc-2"], reason="entry_removed")
    kb.indexer.enqueue_remote_delete(other.id, "ds-y", ["doc-9"], reason="document_deleted")

    rows = kb.repo.list_remote_deletes(project.id)
    pending = kb.service.status(project)["pending_deletes"]
    assert pending == {
        "count": 2,
        "oldest_created_at": min(r.created_at for r in rows),
        "last_error": "RAGFlow: Unauthorized",
    }
    assert kb.service.status(kb.project(name="C"))["pending_deletes"] == {
        "count": 0, "oldest_created_at": None, "last_error": None,
    }


# ------------------------------------------------------------ AC-12: script


def test_script_lists_only_on_the_json_store_without_run(kb):
    # covers: AC-12
    gone_project = uuid4()
    kb.retriever.datasets["ds-old"] = {}
    kb.indexer.enqueue_dataset_delete(gone_project, "ds-old", reason="project_deleted")
    lines: list[str] = []
    code = pending_deletes(run=False, indexer=kb.build_indexer(), repo=kb.repo, out=lines.append)
    assert code == 1
    assert f"project={gone_project} kind=dataset dataset=ds-old" in lines[0]
    assert "reason=project_deleted attempts=0" in lines[0]
    assert lines[-1] == "deleted 0, still pending 1"
    assert kb.retriever.delete_calls == []


def test_script_runs_the_rows_with_run(kb):
    # covers: AC-12
    kb.retriever.datasets["ds-old"] = {}
    kb.indexer.enqueue_dataset_delete(uuid4(), "ds-old", reason="project_deleted")
    lines: list[str] = []
    code = pending_deletes(run=True, indexer=kb.build_indexer(), repo=kb.repo, out=lines.append)
    assert code == 0
    assert lines[-1] == "deleted 1, still pending 0"
    assert "ds-old" not in kb.retriever.datasets


def test_script_records_one_attempt_and_returns_at_once_on_failure(kb):
    # covers: AC-12
    kb.indexer.enqueue_dataset_delete(uuid4(), "ds-old", reason="project_deleted")
    kb.retriever.delete_error = OUTAGE
    script_indexer = kb.build_indexer()
    lines: list[str] = []
    code = pending_deletes(run=True, indexer=script_indexer, repo=kb.repo, out=lines.append)
    assert code == 1
    assert lines[-1] == "deleted 0, still pending 1"
    [row] = kb.repo.list_remote_deletes(None)
    assert row.attempts == 1
    assert script_indexer.pending() == 0  # no short retries scheduled
    assert len(kb.retriever.delete_calls) == 1


def test_script_lists_but_never_runs_with_rag_disabled(kb):
    # covers: AC-12
    kb.indexer.enqueue_dataset_delete(uuid4(), "ds-old", reason="project_deleted")
    kb.retriever.active = False
    lines: list[str] = []
    assert pending_deletes(run=True, indexer=kb.build_indexer(), repo=kb.repo, out=lines.append) == 1
    assert kb.retriever.delete_calls == []


def test_age_format():
    assert format_age(40) == "40s"
    assert format_age(5 * 60 + 3) == "5m 3s"
    assert format_age(2 * 3600 + 5 * 60) == "2h 5m"
    assert format_age(3 * 86400 + 4 * 3600 + 59) == "3d 4h"


# ------------------------------------------------------------ AC-13: RAGFlow answers (mocked HTTP)


class FakeResponse:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    """Answers each (method, path suffix) from a script of responses."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        for (m, suffix), responses in self.answers.items():
            if m == method and url.endswith(suffix):
                return responses.pop(0) if len(responses) > 1 else responses[0]
        raise AssertionError(f"unexpected call {method} {url}")


def _ragflow(answers):
    session = FakeSession(answers)
    return RAGFlowRetriever("http://ragflow", "key", session=session), session


PROJECT = SimpleNamespace(id=uuid4(), rag_dataset_id="ds-1")


def test_short_count_then_an_empty_lookup_is_not_found():
    # covers: AC-13
    retriever, session = _ragflow({
        ("DELETE", "/datasets/ds-1/documents"): [FakeResponse({"code": 0, "data": {"deleted": 0}})],
        ("DELETE", "/datasets/ds-1/chunks"): [FakeResponse({"code": 0})],
        ("GET", "/datasets/ds-1/documents"): [FakeResponse({"code": 0, "data": {"docs": []}})],
    })
    with pytest.raises(RemoteNotFound):
        retriever.delete(PROJECT, ["doc-1"])
    # The parse is stopped before the lookup.
    assert [c[0] for c in session.calls] == ["DELETE", "DELETE", "GET"]


def test_short_count_with_the_document_still_listed_is_transient():
    # covers: AC-13
    retriever, _ = _ragflow({
        ("DELETE", "/datasets/ds-1/documents"): [FakeResponse({"code": 0, "data": {"deleted": 0}})],
        ("DELETE", "/datasets/ds-1/chunks"): [FakeResponse({"code": 0})],
        ("GET", "/datasets/ds-1/documents"): [FakeResponse({"code": 0, "data": {"docs": [{"id": "doc-1"}]}})],
    })
    with pytest.raises(RetrieverError) as raised:
        retriever.delete(PROJECT, ["doc-1"])
    assert raised.value.transient and not isinstance(raised.value, RemoteNotFound)


def test_installed_ragflow_unknown_document_answer_is_not_found_after_lookup():
    # covers: AC-13 (the answers the installed RAGFlow gave on 2026-11-18)
    retriever, _ = _ragflow({
        ("DELETE", "/datasets/ds-1/documents"): [FakeResponse(
            {"code": 102, "message": "These documents do not belong to dataset ds-1 or Document not found: doc-1"})],
        ("GET", "/datasets/ds-1/documents"): [FakeResponse(
            {"code": 102, "message": "you don't own the document doc-1"})],
    })
    with pytest.raises(RemoteNotFound) as raised:
        retriever.delete(PROJECT, ["doc-1"])
    assert raised.value.code == 102


def test_a_102_delete_with_the_document_still_present_keeps_the_error():
    # covers: AC-13
    retriever, _ = _ragflow({
        ("DELETE", "/datasets/ds-1/documents"): [FakeResponse({"code": 102, "message": "Document not found: doc-1"})],
        ("GET", "/datasets/ds-1/documents"): [FakeResponse({"code": 0, "data": {"docs": [{"id": "doc-1"}]}})],
    })
    with pytest.raises(RetrieverError) as raised:
        retriever.delete(PROJECT, ["doc-1"])
    assert not isinstance(raised.value, RemoteNotFound)


@pytest.mark.parametrize("response", [
    FakeResponse({"code": 403, "message": "Permission denied"}),  # the installed RAGFlow
    FakeResponse({"code": 404, "message": "Resource not found"}),
    FakeResponse({}, status=404),
])
def test_dataset_not_found_answers(response):
    # covers: AC-13
    retriever, _ = _ragflow({("DELETE", "/api/v1/datasets"): [response]})
    with pytest.raises(RemoteNotFound):
        retriever.delete_dataset(PROJECT)


def test_an_unrecognised_dataset_answer_stays_a_plain_error():
    # covers: AC-13
    retriever, _ = _ragflow({("DELETE", "/api/v1/datasets"): [FakeResponse({"code": 100, "message": "boom"})]})
    with pytest.raises(RetrieverError) as raised:
        retriever.delete_dataset(PROJECT)
    assert not isinstance(raised.value, RemoteNotFound)
    assert (raised.value.status_code, raised.value.code) == (200, 100)


# ------------------------------------------------------------ PostgreSQL


@pytest.fixture
def pg_repo():
    if get_settings().repository.provider != "postgres":
        pytest.skip("PostgreSQL is not the configured repository")
    try:
        from backend.repository.postgres_project_repository import PostgresProjectRepository, db_session

        repo = PostgresProjectRepository()
        repo.list_remote_deletes(uuid4())
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"PostgreSQL is not reachable: {exc}")
    created: list = []
    yield SimpleNamespace(repo=repo, created=created)
    for row_id in created:
        repo.remove_remote_delete(row_id)
    db_session.remove()


def test_postgres_duplicate_insert_returns_the_existing_row(pg_repo):
    # covers: AC-2 (PostgreSQL)
    repo = pg_repo.repo
    project_id, dataset_id = uuid4(), f"ds-{uuid4().hex}"
    first = repo.add_remote_delete(KnowledgeRemoteDelete(
        project_id=project_id, kind="document", dataset_id=dataset_id, remote_doc_id="doc-1", reason="document_deleted",
    ))
    pg_repo.created.append(first.id)
    again = repo.add_remote_delete(KnowledgeRemoteDelete(
        project_id=project_id, kind="document", dataset_id=dataset_id, remote_doc_id="doc-1", reason="entry_removed",
    ))
    assert again.id == first.id

    # A racing insert that skips the pre check hits the partial unique index,
    # rolls back, returns the winner, and leaves the session usable.
    racer = KnowledgeRemoteDelete(
        project_id=project_id, kind="document", dataset_id=dataset_id, remote_doc_id="doc-1", reason="entry_removed",
    )
    original = repo._existing_remote_delete
    calls = {"n": 0}

    def miss_once(row):
        calls["n"] += 1
        return (False, None) if calls["n"] == 1 else original(row)

    repo._existing_remote_delete = miss_once
    try:
        winner = repo.add_remote_delete(racer)
    finally:
        del repo._existing_remote_delete
    assert winner.id == first.id
    assert [r.id for r in repo.list_remote_deletes(project_id)] == [first.id]


def test_postgres_dataset_row_absorbs_documents_and_records_failures(pg_repo):
    # covers: AC-2, AC-5 (PostgreSQL)
    repo = pg_repo.repo
    project_id, dataset_id = uuid4(), f"ds-{uuid4().hex}"
    doc_row = repo.add_remote_delete(KnowledgeRemoteDelete(
        project_id=project_id, kind="document", dataset_id=dataset_id, remote_doc_id="doc-1", reason="document_deleted",
    ))
    pg_repo.created.append(doc_row.id)
    dataset_row = repo.add_remote_delete(KnowledgeRemoteDelete(
        project_id=project_id, kind="dataset", dataset_id=dataset_id, reason="project_deleted",
    ))
    pg_repo.created.append(dataset_row.id)
    assert [r.kind for r in repo.list_remote_deletes(project_id)] == ["dataset"]
    assert repo.add_remote_delete(KnowledgeRemoteDelete(
        project_id=project_id, kind="document", dataset_id=dataset_id, remote_doc_id="doc-2", reason="document_deleted",
    )) is None
    assert repo.get_dataset_delete(dataset_id).id == dataset_row.id

    at = datetime.now(timezone.utc)
    repo.record_remote_delete_failure(dataset_row.id, "y" * 900, at)
    repo.record_remote_delete_failure(dataset_row.id, "second", at + timedelta(seconds=1))
    row = repo.get_remote_delete(dataset_row.id)
    assert (row.attempts, row.last_error) == (2, "second")
    assert repo.remove_remote_delete(dataset_row.id) is True
    assert repo.remove_remote_delete(dataset_row.id) is False
