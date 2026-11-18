"""RAGFlowRetriever HTTP contract and the retriever factory (spec 0006).

The HTTP boundary is replaced by a scripted session, so no RAGFlow server is
needed. Each test checks what we send to RAGFlow and how we read its answers.
"""

from types import SimpleNamespace

import pytest
import requests

from backend.services.retrieval import factory
from backend.services.retrieval.base import (
    PARSE_DONE,
    PARSE_FAILED,
    PARSE_RUNNING,
    NoDatasetError,
    RetrieverError,
    dataset_name,
    require_dataset,
)
from backend.services.retrieval.fake import NullRetriever
from backend.services.retrieval.ragflow import RAGFlowRetriever

BASE = "http://ragflow.test"


class _Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class ScriptedSession:
    """Stands in for requests.Session: returns queued replies in order and
    records every request. A queued exception is raised instead."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append(SimpleNamespace(method=method, url=url, **kwargs))
        reply = self.replies.pop(0) if self.replies else {"code": 0, "data": None}
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, _Response):
            return reply
        return _Response(reply)


def ok(data=None):
    return {"code": 0, "data": data}


def project(dataset_id="ds-1"):
    return SimpleNamespace(id="p1", name="Demo", rag_dataset_id=dataset_id)


def retriever(*replies, key="secret-key"):
    session = ScriptedSession(*replies)
    return RAGFlowRetriever(BASE, key, session=session), session


# ------------------------------------------------------------ base helpers


def test_dataset_name_is_proj_plus_project_id():
    assert dataset_name(SimpleNamespace(id="abc")) == "proj-abc"


def test_require_dataset_returns_the_projects_dataset():
    assert require_dataset(project("ds-9")) == "ds-9"


@pytest.mark.parametrize("dataset_id", [None, ""])
def test_require_dataset_raises_when_the_project_has_none(dataset_id):
    with pytest.raises(NoDatasetError):
        require_dataset(project(dataset_id))


# ------------------------------------------------------------ _call: transport and errors


@pytest.mark.parametrize("base, key", [("", "k"), (BASE, None), (BASE, "")])
def test_an_unconfigured_retriever_is_inactive_and_makes_no_request(base, key):
    session = ScriptedSession()
    r = RAGFlowRetriever(base, key, session=session)
    assert r.is_active() is False
    with pytest.raises(RetrieverError, match="not configured"):
        r.search(project(), "q", 3)
    assert session.calls == []


def test_requests_carry_the_bearer_key_and_strip_a_trailing_slash():
    session = ScriptedSession(ok([]))
    RAGFlowRetriever(BASE + "/", "secret-key", session=session).search(project(), "q", 3)
    call = session.calls[0]
    assert call.url == f"{BASE}/api/v1/retrieval"
    assert call.headers == {"Authorization": "Bearer secret-key"}
    assert call.timeout > 0


def test_a_nonzero_code_inside_http_200_is_an_error_with_ragflows_message():
    r, _ = retriever({"code": 102, "message": "parse_type is required"})
    with pytest.raises(RetrieverError, match="RAGFlow: parse_type is required") as err:
        r.ensure_dataset(project(None))
    assert err.value.transient is False


def test_a_4xx_response_is_a_permanent_error():
    r, _ = retriever(_Response({"code": 401}, status_code=401))
    with pytest.raises(RetrieverError, match="HTTP 401") as err:
        r.search(project(), "q", 3)
    assert err.value.transient is False


def test_a_5xx_response_is_transient():
    r, _ = retriever(_Response({}, status_code=503))
    with pytest.raises(RetrieverError) as err:
        r.search(project(), "q", 3)
    assert err.value.transient is True


@pytest.mark.parametrize("exc", [requests.ConnectionError("down"), requests.Timeout("slow")])
def test_connection_errors_and_timeouts_are_transient(exc):
    r, _ = retriever(exc)
    with pytest.raises(RetrieverError, match="unreachable") as err:
        r.search(project(), "q", 3)
    assert err.value.transient is True


def test_other_request_errors_are_permanent():
    r, _ = retriever(requests.TooManyRedirects("loop"))
    with pytest.raises(RetrieverError, match="request failed") as err:
        r.search(project(), "q", 3)
    assert err.value.transient is False


def test_a_response_without_json_is_an_error():
    r, _ = retriever(_Response(ValueError("not json")))
    with pytest.raises(RetrieverError, match="without JSON"):
        r.search(project(), "q", 3)


def test_the_api_key_never_appears_in_an_error_message():
    r, _ = retriever({"code": 109, "message": "Authentication error"}, key="sk-very-secret")
    with pytest.raises(RetrieverError) as err:
        r.search(project(), "q", 3)
    assert "sk-very-secret" not in str(err.value)


# ------------------------------------------------------------ ensure_dataset (AC-2)


def test_ensure_dataset_reuses_the_projects_dataset_without_calling_ragflow():
    r, session = retriever()
    assert r.ensure_dataset(project("ds-existing")) == "ds-existing"
    assert session.calls == []


def test_ensure_dataset_creates_proj_dataset_with_builtin_parse_type():
    # Regression: RAGFlow v1.0 rejects a create without parse_type
    # ("parse_type is required") and needs parser_id when parse_type is 1.
    r, session = retriever(ok({"id": "ds-new"}))
    assert r.ensure_dataset(project(None)) == "ds-new"
    call = session.calls[0]
    assert (call.method, call.url) == ("POST", f"{BASE}/api/v1/datasets")
    assert call.json == {"name": "proj-p1", "parse_type": 1, "parser_id": "general"}


def test_ensure_dataset_fails_when_ragflow_returns_no_id():
    r, _ = retriever(ok({}))
    with pytest.raises(RetrieverError, match="without an id"):
        r.ensure_dataset(project(None))


# ------------------------------------------------------------ upload and start parsing (AC-1, AC-3)


def test_upload_file_sends_the_file_then_starts_parsing_it(tmp_path):
    path = tmp_path / "spec.txt"
    path.write_text("hello")
    r, session = retriever(ok([{"id": "doc-7"}]), ok())

    result = r.upload_file(project(), path, "spec.txt")

    assert result.remote_doc_id == "doc-7"
    upload, parse = session.calls
    assert (upload.method, upload.url) == ("POST", f"{BASE}/api/v1/datasets/ds-1/documents")
    assert upload.files["file"][0] == "spec.txt"
    assert (parse.method, parse.url) == ("POST", f"{BASE}/api/v1/datasets/ds-1/chunks")
    assert parse.json == {"document_ids": ["doc-7"]}


def test_upload_text_sends_utf8_plain_text():
    r, session = retriever(ok({"id": "doc-8"}), ok())
    result = r.upload_text(project(), "café", "notes.txt")
    assert result.remote_doc_id == "doc-8"
    assert session.calls[0].files["file"] == ("notes.txt", "café".encode("utf-8"), "text/plain")


@pytest.mark.parametrize("data", [[], [{}], {}, None])
def test_an_upload_without_a_document_id_fails_and_does_not_start_parsing(data):
    r, session = retriever(ok(data))
    with pytest.raises(RetrieverError, match="no document id"):
        r.upload_text(project(), "x", "a.txt")
    assert len(session.calls) == 1


def test_upload_needs_a_dataset_and_makes_no_request(tmp_path):
    r, session = retriever()
    with pytest.raises(NoDatasetError):
        r.upload_text(project(None), "x", "a.txt")
    assert session.calls == []


# ------------------------------------------------------------ parse_status (AC-3)


@pytest.mark.parametrize(
    "run, expected",
    [
        ("DONE", PARSE_DONE),
        ("3", PARSE_DONE),
        ("FAIL", PARSE_FAILED),
        ("CANCEL", PARSE_FAILED),
        ("4", PARSE_FAILED),
        ("2", PARSE_FAILED),
        ("RUNNING", PARSE_RUNNING),
        ("UNSTART", PARSE_RUNNING),
        ("1", PARSE_RUNNING),
        ("", PARSE_RUNNING),
    ],
)
def test_parse_status_maps_the_ragflow_run_value(run, expected):
    r, session = retriever(ok({"docs": [{"id": "doc-1", "run": run}]}))
    assert r.parse_status(project(), "doc-1").state == expected
    assert session.calls[0].params == {"id": "doc-1"}


def test_a_failed_parse_carries_ragflows_progress_message():
    r, _ = retriever(ok({"docs": [{"id": "doc-1", "run": "FAIL", "progress_msg": "bad layout"}]}))
    assert r.parse_status(project(), "doc-1").message == "bad layout"


@pytest.mark.parametrize(
    "ingestion_status, expected",
    [
        ("COMPLETED", PARSE_DONE),
        ("FAILED", PARSE_FAILED),
        ("STOPPED", PARSE_FAILED),
        ("CREATED", PARSE_RUNNING),
        ("SCHEDULED", PARSE_RUNNING),
        ("RUNNING", PARSE_RUNNING),
        ("STOPPING", PARSE_RUNNING),
    ],
)
def test_parse_status_maps_the_ragflow_v1_ingestion_status(ingestion_status, expected):
    # RAGFlow v1.0 sends `ingestion_status` and no `run` field.
    r, _ = retriever(ok({"docs": [{"id": "doc-1", "run": None, "progress": 0.8, "ingestion_status": ingestion_status}]}))
    assert r.parse_status(project(), "doc-1").state == expected


def test_a_v1_failed_ingestion_carries_the_latest_event_message():
    doc = {
        "id": "doc-1",
        "run": None,
        "progress_msg": "",
        "ingestion_status": "FAILED",
        "latest_ingestion_event": {"message": "Task failed: embedding requested but encoder resolution returned nil"},
    }
    r, _ = retriever(ok({"docs": [doc]}))
    status = r.parse_status(project(), "doc-1")
    assert status.state == PARSE_FAILED
    assert "encoder resolution returned nil" in status.message


def test_parse_status_reads_a_plain_list_and_the_documents_key():
    r, _ = retriever(ok([{"id": "doc-1", "run": "DONE"}]), ok({"documents": [{"id": "doc-1", "run": "DONE"}]}))
    assert r.parse_status(project(), "doc-1").state == PARSE_DONE
    assert r.parse_status(project(), "doc-1").state == PARSE_DONE


def test_a_document_missing_from_ragflow_counts_as_failed():
    r, _ = retriever(ok({"docs": [{"id": "someone-else", "run": "DONE"}]}))
    status = r.parse_status(project(), "doc-1")
    assert status.state == PARSE_FAILED
    assert "not found" in status.message


# ------------------------------------------------------------ delete (AC-5 scoping)


def test_delete_with_no_ids_makes_no_request():
    r, session = retriever()
    r.delete(project(), [])
    r.delete(project(), ["", None])
    assert session.calls == []


def test_delete_drops_empty_ids_and_targets_only_the_projects_dataset():
    r, session = retriever(ok())
    r.delete(project("ds-1"), ["doc-1", "", "doc-2"])
    call = session.calls[0]
    assert (call.method, call.url) == ("DELETE", f"{BASE}/api/v1/datasets/ds-1/documents")
    assert call.json == {"ids": ["doc-1", "doc-2"]}


def test_delete_of_a_document_still_parsing_stops_the_parse_and_is_transient():
    # RAGFlow answers code 0 with deleted 0 while ingestion is RUNNING, and
    # removes nothing. Treating that as success left an orphan in the dataset.
    # Spec 0007, AC-13: a lookup that still lists the document confirms it is parsing.
    r, session = retriever(ok({"deleted": 0}), ok(), ok({"docs": [{"id": "doc-1"}]}))
    with pytest.raises(RetrieverError) as caught:
        r.delete(project("ds-1"), ["doc-1"])
    assert caught.value.transient is True
    assert "still parsing" in str(caught.value)
    stop = session.calls[1]
    assert (stop.method, stop.url) == ("DELETE", f"{BASE}/api/v1/datasets/ds-1/chunks")
    assert stop.json == {"document_ids": ["doc-1"]}


def test_a_failed_stop_request_still_reports_the_delete_as_transient():
    r, session = retriever(ok({"deleted": 0}), {"code": 102, "message": "busy"}, ok({"docs": [{"id": "doc-1"}]}))
    with pytest.raises(RetrieverError) as caught:
        r.delete(project("ds-1"), ["doc-1"])
    assert caught.value.transient is True
    assert len(session.calls) == 3  # delete, stop the parse, then the AC-13 lookup


def test_delete_that_removes_every_document_makes_no_stop_request():
    r, session = retriever(ok({"deleted": 2}))
    r.delete(project("ds-1"), ["doc-1", "doc-2"])
    assert len(session.calls) == 1


def test_delete_dataset_sends_the_projects_dataset_id():
    r, session = retriever(ok())
    r.delete_dataset(project("ds-1"))
    assert (session.calls[0].method, session.calls[0].json) == ("DELETE", {"ids": ["ds-1"]})


# ------------------------------------------------------------ search (AC-12)


def test_search_queries_only_the_projects_dataset():
    r, session = retriever(ok({"chunks": []}))
    r.search(project("ds-a"), "discount", 4)
    assert session.calls[0].json == {"question": "discount", "dataset_ids": ["ds-a"], "top_k": 4}


def test_search_maps_ragflow_chunks():
    r, _ = retriever(
        ok(
            {
                "chunks": [
                    {"id": "c1", "content": "Members save 10%", "similarity": 0.91, "document_id": "doc-1", "document_keyword": "spec.txt"},
                    {"content_with_weight": "Old field names", "similarity": "0.5", "doc_id": "doc-2", "docnm_kwd": "old.md"},
                ]
            }
        )
    )
    first, second = r.search(project(), "q", 5)
    assert (first.chunk_id, first.text, first.score, first.remote_doc_id, first.title) == (
        "c1", "Members save 10%", 0.91, "doc-1", "spec.txt",
    )
    assert (second.text, second.score, second.remote_doc_id, second.title) == ("Old field names", 0.5, "doc-2", "old.md")
    assert second.chunk_id == "doc-2:1"


def test_search_skips_chunks_without_text_or_document_and_caps_at_top_k():
    chunks = [
        {"content": "", "document_id": "doc-1"},
        {"content": "no doc"},
        {"content": "a", "document_id": "doc-1"},
        {"content": "b", "document_id": "doc-1"},
        {"content": "c", "document_id": "doc-1"},
    ]
    r, _ = retriever(ok({"chunks": chunks}))
    assert [c.text for c in r.search(project(), "q", 2)] == ["a", "b"]


def test_search_with_no_dataset_raises_before_any_request():
    r, session = retriever()
    with pytest.raises(NoDatasetError):
        r.search(project(None), "q", 3)
    assert session.calls == []


# ------------------------------------------------------------ factory


def _settings(enabled, provider):
    return SimpleNamespace(
        rag=SimpleNamespace(enabled=enabled, vector_db_provider=provider, ragflow_api_base=BASE, ragflow_api_key="k")
    )


@pytest.mark.parametrize(
    "enabled, provider, active",
    [(True, "ragflow", True), (False, "ragflow", False), (True, "chroma", False), (False, "chroma", False)],
)
def test_rag_is_enabled_only_for_the_ragflow_provider(enabled, provider, active):
    assert factory.rag_enabled(_settings(enabled, provider)) is active


def test_build_retriever_returns_ragflow_when_enabled():
    built = factory.build_retriever(_settings(True, "ragflow"))
    assert isinstance(built, RAGFlowRetriever)
    assert (built.api_base, built.api_key) == (BASE, "k")


def test_build_retriever_returns_an_inactive_null_retriever_when_disabled():
    built = factory.build_retriever(_settings(False, "ragflow"))
    assert isinstance(built, NullRetriever)
    assert built.is_active() is False


@pytest.fixture
def restore_retriever():
    saved = factory._retriever
    yield
    factory.set_retriever(saved)


def test_get_retriever_returns_the_swapped_in_retriever_every_time(restore_retriever):
    fake = NullRetriever()
    factory.set_retriever(fake)
    assert factory.get_retriever() is fake
    assert factory.get_retriever() is fake


def test_set_retriever_none_rebuilds_from_settings_once(restore_retriever, monkeypatch):
    built = []
    monkeypatch.setattr(factory, "build_retriever", lambda settings=None: built.append(1) or NullRetriever())
    factory.set_retriever(None)
    first = factory.get_retriever()
    assert factory.get_retriever() is first
    assert built == [1]
