"""Per project knowledge base, slice 1 (spec 0006).

Upload, indexing, search, and the search_knowledge chat tool, all against
the InMemoryRetriever and a fake clock: no RAGFlow, no sleeping.
"""

import io
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.repository.project_repository as repo_module
from backend.config.settings import get_settings
from backend.main import app
import backend.main as main_module
from backend.repository.project_repository import JSONProjectRepository
from backend.services.chat_tools import ChatToolbox
from backend.services.knowledge_indexer import (
    DISABLED_ERROR,
    FILE_NOT_FOUND,
    PARSE_TIMED_OUT,
    KnowledgeIndexer,
)
from backend.services.knowledge_service import (
    KnowledgeNotReady,
    KnowledgeService,
    KnowledgeUploadError,
    sanitize_filename,
)
from backend.services.retrieval import PARSE_FAILED, PARSE_RUNNING, ParseStatus
from backend.services.retrieval.fake import InMemoryRetriever, NullRetriever


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += seconds


@pytest.fixture
def settings(tmp_path):
    base = get_settings().model_copy(deep=True)
    base.rag.storage_root = str(tmp_path / "projects")
    base.rag.upload_max_mb = 1
    base.rag.parse_poll_seconds = 5
    base.rag.parse_timeout_seconds = 600
    return base


@pytest.fixture
def kb(tmp_path, settings):
    repo = JSONProjectRepository(file_path=tmp_path / "store.json")
    retriever = InMemoryRetriever()
    clock = FakeClock()
    indexer = KnowledgeIndexer(retriever=retriever, repository=repo, settings=settings, clock=clock)
    service = KnowledgeService(repository=repo, retriever=retriever, indexer=indexer, settings=settings)

    def project(name="A", url="https://a.example.com"):
        return repo.create_project(name=name, description="d", target_url=url)

    def upload(proj, text="The checkout page applies a 10 percent discount for members.", name="spec.txt"):
        return service.upload(proj, io.BytesIO(text.encode()), name, "text/plain")

    def index_all():
        indexer.run_pending()
        clock.advance(settings.rag.parse_poll_seconds)
        indexer.run_pending()

    from types import SimpleNamespace

    return SimpleNamespace(
        repo=repo, retriever=retriever, clock=clock, indexer=indexer, service=service,
        project=project, upload=upload, index_all=index_all, settings=settings,
    )


# ------------------------------------------------------------ AC-1, AC-2, AC-3, AC-12


def test_upload_index_and_search_happy_path(kb):
    project = kb.project()
    doc = kb.upload(project)

    saved_path = Path(doc.storage_path)
    assert saved_path.read_text() == "The checkout page applies a 10 percent discount for members."
    assert saved_path.parts[-4:] == (str(project.id), "documents", str(doc.id), "spec.txt")
    assert doc.embedding_status == "pending"

    kb.index_all()

    stored = kb.repo.get_document(project.id, doc.id)
    assert stored.embedding_status == "completed"
    assert stored.remote_doc_id
    assert stored.error is None
    assert len(kb.retriever.created_datasets) == 1
    assert kb.repo.get_project(project.id).rag_dataset_id == kb.retriever.created_datasets[0]

    items = kb.service.search(project, "discount for members")
    assert items and items[0]["source_type"] == "document"
    assert items[0]["source_ref"] == str(doc.id)
    assert items[0]["title"] == "spec.txt"

    # A second file reuses the same dataset.
    kb.upload(project, "Refunds take five days.", "refunds.md")
    kb.index_all()
    assert len(kb.retriever.created_datasets) == 1


def test_search_never_crosses_projects(kb):
    a = kb.project("A", "https://a.example.com")
    b = kb.project("B", "https://b.example.com")
    kb.upload(a, "Project A secret pricing rules.", "spec.txt")
    kb.upload(b, "Project B shipping rules.", "spec.txt")
    kb.index_all()

    assert kb.repo.get_project(a.id).rag_dataset_id != kb.repo.get_project(b.id).rag_dataset_id
    results = kb.service.search(b, "pricing shipping rules secret")
    assert results and all("Project B" in item["text"] for item in results)


def test_search_drops_repeated_passages(kb):
    # Review 2026-11-18: the same file uploaded twice must not fill the results twice.
    project = kb.project()
    kb.upload(project, "Login locks after five attempts.", "a.txt")
    kb.upload(project, "  login LOCKS after five   attempts. ", "b.txt")
    kb.upload(project, "Passwords expire after ninety days.", "c.txt")
    kb.index_all()

    texts = [item["text"] for item in kb.service.search(project, "login locks attempts passwords", top_k=5)]
    assert len(texts) == 2
    assert any("ninety days" in text for text in texts)


def test_chunks_that_do_not_map_to_a_local_record_are_dropped(kb):
    project = kb.project()
    doc = kb.upload(project, "Login locks after five attempts.")
    kb.index_all()
    kb.repo.delete_document(project.id, doc.id)  # remote copy still there
    assert kb.service.search(project, "login attempts") == []


def test_search_needs_rag_and_a_dataset(kb):
    project = kb.project()
    with pytest.raises(KnowledgeNotReady):
        kb.service.search(project, "anything")  # no dataset yet
    with pytest.raises(ValueError):
        kb.service.search(project, "x", source_types=["nonsense"])


def test_parse_failure_and_timeout(kb):
    project = kb.project()
    doc = kb.upload(project)
    kb.indexer.run_pending()
    remote = kb.repo.get_document(project.id, doc.id).remote_doc_id

    kb.retriever.parse_states[remote] = ParseStatus(PARSE_RUNNING)
    kb.clock.advance(5)
    kb.indexer.run_pending()
    assert kb.repo.get_document(project.id, doc.id).embedding_status == "processing"

    kb.clock.advance(600)
    kb.indexer.run_pending()
    failed = kb.repo.get_document(project.id, doc.id)
    assert failed.embedding_status == "failed"
    assert failed.error == PARSE_TIMED_OUT

    other = kb.upload(project, "Second file text.", "two.txt")
    kb.indexer.run_pending()
    other_remote = kb.repo.get_document(project.id, other.id).remote_doc_id
    kb.retriever.parse_states[other_remote] = ParseStatus(PARSE_FAILED, "unsupported layout")
    kb.clock.advance(5)
    kb.indexer.run_pending()
    assert kb.repo.get_document(project.id, other.id).error == "unsupported layout"


def test_missing_file_fails_without_upload(kb):
    project = kb.project()
    doc = kb.upload(project)
    Path(doc.storage_path).unlink()
    kb.indexer.run_pending()
    stored = kb.repo.get_document(project.id, doc.id)
    assert stored.embedding_status == "failed"
    assert stored.error == FILE_NOT_FOUND
    assert kb.retriever.created_datasets == []


def test_document_deleted_while_uploading_leaves_no_remote_copy(kb):
    project = kb.project()
    doc = kb.upload(project)
    original = kb.retriever.upload_file

    def upload_then_delete(proj, path, name):
        result = original(proj, path, name)
        kb.repo.delete_document(project.id, doc.id)
        return result

    kb.retriever.upload_file = upload_then_delete
    kb.indexer.run_pending()
    dataset = kb.retriever.datasets[kb.repo.get_project(project.id).rag_dataset_id]
    assert dataset == {}


def test_concurrent_first_indexes_create_one_dataset(kb):
    project = kb.project()
    barrier = threading.Barrier(2)
    original = kb.retriever.ensure_dataset

    def slow_ensure(proj):
        return original(proj)

    kb.retriever.ensure_dataset = slow_ensure
    errors = []

    def run():
        try:
            barrier.wait()
            kb.indexer._ensure_dataset(project)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(kb.retriever.created_datasets) == 1


def test_a_losing_dataset_writer_deletes_its_extra_dataset(kb):
    project = kb.project()
    kb.repo.set_project_rag_dataset(project.id, "ds-winner")
    kb.retriever.datasets["ds-winner"] = {}
    stale = project  # still has no rag_dataset_id
    original_get = kb.repo.get_project
    kb.repo.get_project = lambda pid, include_deleted=False: stale  # the lock read sees the stale copy
    resolved = kb.indexer._ensure_dataset(stale)
    kb.repo.get_project = original_get
    assert resolved.rag_dataset_id == "ds-winner"
    # Spec 0007, AC-10: the extra dataset is queued as a pending delete, then the worker removes it.
    extra = kb.retriever.created_datasets[0]
    [row] = kb.repo.list_remote_deletes(project.id)
    assert (row.kind, row.dataset_id, row.reason) == ("dataset", extra, "duplicate_dataset")
    kb.indexer.run_pending()
    assert extra not in kb.retriever.datasets
    assert "ds-winner" in kb.retriever.datasets
    assert kb.repo.list_remote_deletes(None) == []


def test_stale_project_update_keeps_the_dataset(kb):
    project = kb.project()
    kb.repo.set_project_rag_dataset(project.id, "ds-1")
    project.description = "edited from a stale copy"
    kb.repo.update_project(project)
    assert kb.repo.get_project(project.id).rag_dataset_id == "ds-1"


# ------------------------------------------------------------ AC-1 limits and names, AC-8


def test_upload_limits(kb):
    project = kb.project()
    with pytest.raises(KnowledgeUploadError) as too_big:
        kb.service.upload(project, io.BytesIO(b"x" * (1024 * 1024 + 1)), "big.txt", "text/plain")
    assert too_big.value.status_code == 413
    with pytest.raises(KnowledgeUploadError) as wrong_type:
        kb.service.upload(project, io.BytesIO(b"MZ"), "tool.exe", "application/octet-stream")
    assert wrong_type.value.status_code == 415
    with pytest.raises(KnowledgeUploadError) as empty:
        kb.service.upload(project, io.BytesIO(b""), "empty.txt", "text/plain")
    assert empty.value.status_code == 422
    # Nothing is left behind by a rejected upload.
    root = Path(kb.settings.rag.storage_root)
    assert not any(p.is_file() for p in root.rglob("*")) if root.exists() else True
    assert kb.repo.get_documents(project.id) == []


def test_content_type_is_only_a_hint(kb):
    project = kb.project()
    doc = kb.service.upload(project, io.BytesIO(b"# Notes"), "notes.md", "application/octet-stream")
    assert doc.filename == "notes.md"


def test_same_name_twice_gets_separate_folders(kb):
    project = kb.project()
    first = kb.upload(project)
    second = kb.upload(project)
    assert first.storage_path != second.storage_path
    assert Path(first.storage_path).exists() and Path(second.storage_path).exists()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("../x.pdf", "x.pdf"),
        ("..\\..\\evil.txt", "evil.txt"),
        ("CON.txt", "_CON.txt"),
        ("nul", "_nul"),
        ("name.", "name"),
        ("report .pdf ", "report.pdf"),
        ("a..b.txt", "a.b.txt"),
        ("we<ird>:name?.csv", "we_ird__name_.csv"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_caps_length_and_keeps_extension():
    name = sanitize_filename("a" * 300 + ".docx")
    assert len(name) == 120 and name.endswith(".docx")


def test_disabled_rag_saves_the_file_and_waits(kb):
    service = KnowledgeService(repository=kb.repo, retriever=NullRetriever(), indexer=kb.indexer, settings=kb.settings)
    project = kb.project()
    doc = service.upload(project, io.BytesIO(b"hello"), "a.txt", "text/plain")
    assert doc.embedding_status == "pending"
    assert doc.error == DISABLED_ERROR
    assert Path(doc.storage_path).exists()
    assert kb.indexer.pending() == 0


# ------------------------------------------------------------ AC-12 chat tool


def test_search_knowledge_tool(kb):
    project = kb.project()
    long_text = "Members get free shipping. " + "detail " * 200
    kb.upload(project, long_text, "shipping.txt")
    toolbox = ChatToolbox(kb.repo, None, result_chars=4000, knowledge_service=kb.service)

    not_ready = json.loads(toolbox.run(project, "search_knowledge", {"query": "shipping"}))
    assert not_ready == {"error": "the knowledge base is not set up for this project"}

    kb.index_all()
    result = json.loads(toolbox.run(project, "search_knowledge", {"query": "free shipping members", "top_k": 3}))
    assert result["items"], result
    item = result["items"][0]
    assert set(item) == {"text", "score", "source_type", "source_ref", "title"}
    assert item["title"] == "shipping.txt"
    assert len(item["text"]) <= 600 + len("…[truncated]")

    filtered = json.loads(toolbox.run(project, "search_knowledge", {"query": "shipping", "source_types": ["test_case"]}))
    assert filtered == {"items": []}
    missing = json.loads(toolbox.run(project, "search_knowledge", {}))
    assert missing == {"error": "query is required"}


@pytest.fixture
def twelve_documents(kb):
    project = kb.project()
    for n in range(12):
        kb.upload(project, f"Refund policy clause {n}: refunds take five days.", f"refund-{n}.txt")
    kb.index_all()
    return project, ChatToolbox(kb.repo, None, result_chars=20000, knowledge_service=kb.service)


@pytest.mark.parametrize(
    "top_k, expected",
    [(None, 5), (3, 3), (10, 10), (50, 10), (0, 5), ("not a number", 5)],
)
def test_search_knowledge_top_k_defaults_to_5_and_never_exceeds_10(twelve_documents, top_k, expected):
    # covers: AC-12
    project, toolbox = twelve_documents
    arguments = {"query": "refund policy refunds"}
    if top_k is not None:
        arguments["top_k"] = top_k
    result = json.loads(toolbox.run(project, "search_knowledge", arguments))
    assert len(result["items"]) == expected


def test_search_knowledge_rejects_an_unknown_source_type(twelve_documents):
    # covers: AC-12
    project, toolbox = twelve_documents
    result = json.loads(toolbox.run(project, "search_knowledge", {"query": "refund", "source_types": ["emails"]}))
    assert "unknown source_types: emails" in result["error"]


def test_search_knowledge_accepts_every_specced_source_type(twelve_documents):
    # covers: AC-12
    project, toolbox = twelve_documents
    every_type = ["document", "jira_story", "test_case", "execution_failure", "defect", "locator"]
    result = json.loads(toolbox.run(project, "search_knowledge", {"query": "refund", "source_types": every_type}))
    assert result["items"] and all(item["source_type"] == "document" for item in result["items"])


def test_chat_system_prompt_steers_the_assistant_to_search_and_cite():
    # covers: AC-13
    from backend.services.chat_service import PROMPT_DIR, _load_text

    text = _load_text(PROMPT_DIR / "system_prompt.txt")
    assert "search_knowledge" in text
    assert "Name the source titles you relied on" in text
    assert "rephrase the query and search once more" in text


# ------------------------------------------------------------ AC-1, AC-6 routes


@pytest.fixture
def api(tmp_path, settings, monkeypatch):
    repo = JSONProjectRepository(file_path=tmp_path / "api_store.json")
    monkeypatch.setattr(repo_module, "_active_project_repo", repo)
    retriever = InMemoryRetriever()
    clock = FakeClock()
    indexer = KnowledgeIndexer(retriever=retriever, repository=repo, settings=settings, clock=clock)
    service = KnowledgeService(repository=repo, retriever=retriever, indexer=indexer, settings=settings)
    monkeypatch.setattr(main_module, "knowledge_service", service)
    from types import SimpleNamespace

    return SimpleNamespace(client=TestClient(app), repo=repo, indexer=indexer, clock=clock)


def _new_project(client, name="KB", url="https://kb.example.com"):
    response = client.post("/api/v1/projects", json={"name": name, "description": "d", "framework": "playwright", "target_url": url})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_upload_route_list_and_delete(api):
    project_id = _new_project(api.client)
    response = api.client.post(
        f"/api/v1/projects/{project_id}/documents/upload",
        files={"file": ("Product Spec.md", b"# Checkout\nMembers get 10 percent off.", "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["original_filename"] == "Product Spec.md"
    assert doc["embedding_status"] == "pending"
    assert "remote_doc_id" in doc and "error" in doc

    api.indexer.run_pending()
    api.clock.advance(5)
    api.indexer.run_pending()
    listed = api.client.get(f"/api/v1/projects/{project_id}/documents").json()
    assert [d["embedding_status"] for d in listed] == ["completed"]

    assert api.client.delete(f"/api/v1/projects/{project_id}/documents/{doc['id']}").status_code == 200
    assert api.client.get(f"/api/v1/projects/{project_id}/documents").json() == []


def test_upload_route_errors(api):
    project_id = _new_project(api.client)
    url = f"/api/v1/projects/{project_id}/documents/upload"
    assert api.client.post(url, files={"file": ("a.exe", b"MZ", "application/octet-stream")}).status_code == 415
    assert api.client.post(url, files={"file": ("a.txt", b"", "text/plain")}).status_code == 422
    big = b"x" * (1024 * 1024 + 1)
    assert api.client.post(url, files={"file": ("a.txt", big, "text/plain")}).status_code == 413


def test_old_json_create_route_is_gone(api):
    project_id = _new_project(api.client)
    response = api.client.post(f"/api/v1/projects/{project_id}/documents", json={"filename": "x.pdf", "original_filename": "x.pdf"})
    assert response.status_code == 405


def test_document_in_another_project_is_not_found(api):
    a = _new_project(api.client, "A", "https://a.example.com")
    b = _new_project(api.client, "B", "https://b.example.com")
    doc = api.client.post(
        f"/api/v1/projects/{a}/documents/upload", files={"file": ("a.txt", b"hello", "text/plain")}
    ).json()
    assert api.client.delete(f"/api/v1/projects/{b}/documents/{doc['id']}").status_code == 404
    assert len(api.client.get(f"/api/v1/projects/{a}/documents").json()) == 1



# ------------------------------------------------------------ AC-6 Knowledge permissions
#
# Under pytest a request without a token is treated as the Superadmin, so
# these tests sign in a real seeded role instead. Grants come from the real
# seeder, loaded into an in-memory SQLite database.


@pytest.fixture(scope="module")
def roles_db():
    # Module scope: seeding hashes the dev passwords, which is slow. Tests
    # only add users with unique emails, so sharing the database is safe.
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.database.db import Base
    from backend.database.db_seeder import seed_database

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.connect() as conn:
        # The app enables SQLite foreign keys for every engine; this test
        # assigns users to JSON store projects that have no projects row.
        conn.execute(text("PRAGMA foreign_keys=OFF"))
    # Only the auth tables: the full schema has Postgres only defaults.
    auth_tables = ["users", "roles", "permissions", "role_permissions", "project_users"]
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in auth_tables])
    session = sessionmaker(bind=engine)()
    seed_database(session)
    yield session
    session.close()
    engine.dispose()


def _user(db, role):
    from uuid import uuid4

    from backend.database.db_models import UserDB

    user = UserDB(id=uuid4(), email=f"{uuid4().hex}@example.com", full_name=role, role=role, password_hash="unused", is_active=True)
    db.add(user)
    db.commit()
    return user


def _assign(db, user, project_id, role_name):
    from uuid import UUID

    from backend.database.db_models import ProjectUserDB, RoleDB

    role = db.query(RoleDB).filter(RoleDB.name == role_name).one()
    db.add(ProjectUserDB(project_id=UUID(str(project_id)), user_id=user.id, role_id=role.id))
    db.commit()


@pytest.mark.parametrize(
    "role, action, allowed",
    [
        ("Stakeholder", "view", True),
        ("Stakeholder", "create", False),
        ("Stakeholder", "delete", False),
        ("Tester", "view", True),
        ("Tester", "create", True),
        ("Tester", "delete", False),
        ("Project Manager", "view", True),
        ("Project Manager", "create", True),
        ("Project Manager", "delete", True),
    ],
)
def test_seeded_knowledge_grants(roles_db, role, action, allowed):
    # covers: AC-6
    from backend.dependencies.auth_dependencies import check_permission

    user = _user(roles_db, role)
    assert check_permission(roles_db, user, None, "Knowledge", action) is allowed


@pytest.fixture
def signed_in(api, roles_db):
    from backend.dependencies.auth_dependencies import get_current_user, get_db

    def sign_in(user):
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = lambda: roles_db

    yield sign_in
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_a_stakeholder_can_list_documents_but_not_upload_or_delete(api, roles_db, signed_in):
    # covers: AC-6
    project_id = _new_project(api.client)
    existing = api.client.post(
        f"/api/v1/projects/{project_id}/documents/upload", files={"file": ("a.txt", b"hello", "text/plain")}
    ).json()
    stakeholder = _user(roles_db, "Stakeholder")
    _assign(roles_db, stakeholder, project_id, "Stakeholder")
    signed_in(stakeholder)

    base = f"/api/v1/projects/{project_id}/documents"
    listed = api.client.get(base)
    upload = api.client.post(f"{base}/upload", files={"file": ("b.txt", b"nope", "text/plain")})
    delete = api.client.delete(f"{base}/{existing['id']}")

    assert listed.status_code == 200 and [d["id"] for d in listed.json()] == [existing["id"]]
    assert (upload.status_code, delete.status_code) == (403, 403)
    assert len(api.repo.get_documents(project_id)) == 1


def test_a_user_not_on_the_project_gets_403_on_every_document_route(api, roles_db, signed_in):
    # covers: AC-6
    project_id = _new_project(api.client)
    outsider = _user(roles_db, "Tester")  # has Knowledge view and create globally, but no project access
    signed_in(outsider)

    base = f"/api/v1/projects/{project_id}/documents"
    responses = [
        api.client.get(base),
        api.client.post(f"{base}/upload", files={"file": ("a.txt", b"hi", "text/plain")}),
    ]

    assert [r.status_code for r in responses] == [403, 403]
    assert api.repo.get_documents(project_id) == []


# ============================================================ slice 2
# Safe and complete knowledge base: retries, sweep, delete, status,
# migration, project cleanup, and the settings key.

import os
from uuid import UUID, uuid4

from backend.models.document import OLD_DOCUMENT_ERROR, Document
from backend.repository.project_repository import KNOWLEDGE_MIGRATION_MARKER
from backend.services.knowledge_service import knowledge_settings_overrides
from backend.services.retrieval import RetrieverError


def _flaky(retriever, method, failures, transient=True):
    """Make a retriever method fail `failures` times before working."""
    original = getattr(retriever, method)
    calls = {"n": 0}

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise RetrieverError("RAGFlow returned 503", transient=transient)
        return original(*args, **kwargs)

    setattr(retriever, method, wrapper)
    return calls


def _legacy_document(project_id, storage_path):
    return Document(
        project_id=project_id, filename="old.pdf", original_filename="old.pdf",
        mime_type="application/pdf", size=10, storage_path=storage_path,
    )


# ------------------------------------------------------------ AC-4 retries


def test_upload_retries_after_transient_failures_then_completes(kb):
    # covers: AC-4
    kb.settings.rag.index_retry_delays = [10, 60, 300]
    calls = _flaky(kb.retriever, "upload_file", failures=2)
    project = kb.project()
    doc = kb.upload(project)

    kb.indexer.run_pending()
    assert kb.repo.get_document(project.id, doc.id).embedding_status == "processing"
    kb.clock.advance(10)
    kb.indexer.run_pending()
    kb.clock.advance(60)
    kb.indexer.run_pending()
    kb.clock.advance(5)
    kb.indexer.run_pending()

    assert calls["n"] == 3
    stored = kb.repo.get_document(project.id, doc.id)
    assert stored.embedding_status == "completed" and stored.error is None


def test_upload_fails_with_the_last_error_after_every_retry(kb):
    # covers: AC-4
    kb.settings.rag.index_retry_delays = [10, 60, 300]
    calls = _flaky(kb.retriever, "upload_file", failures=99)
    project = kb.project()
    doc = kb.upload(project)

    kb.indexer.run_pending()
    for delay in (10, 60, 300):
        assert kb.repo.get_document(project.id, doc.id).embedding_status == "processing"
        kb.clock.advance(delay)
        kb.indexer.run_pending()

    assert calls["n"] == 4  # the first try plus three retries
    stored = kb.repo.get_document(project.id, doc.id)
    assert (stored.embedding_status, stored.error) == ("failed", "RAGFlow returned 503")
    assert kb.indexer.pending() == 0


def test_a_non_transient_failure_is_not_retried(kb):
    # covers: AC-4
    calls = _flaky(kb.retriever, "upload_file", failures=99, transient=False)
    project = kb.project()
    doc = kb.upload(project)
    kb.indexer.run_pending()
    assert calls["n"] == 1
    assert kb.repo.get_document(project.id, doc.id).embedding_status == "failed"


def test_parse_status_errors_are_retried(kb):
    # covers: AC-4
    kb.settings.rag.index_retry_delays = [10]
    project = kb.project()
    doc = kb.upload(project)
    kb.indexer.run_pending()
    _flaky(kb.retriever, "parse_status", failures=1)
    kb.clock.advance(5)
    kb.indexer.run_pending()
    assert kb.repo.get_document(project.id, doc.id).embedding_status == "processing"
    kb.clock.advance(10)
    kb.indexer.run_pending()
    assert kb.repo.get_document(project.id, doc.id).embedding_status == "completed"


# ------------------------------------------------------------ AC-4 startup sweep, AC-8


def test_startup_sweep_requeues_pending_and_processing_documents(kb):
    # covers: AC-4
    project = kb.project()
    never_uploaded = kb.upload(project, "Pending text.", "pending.txt")
    uploaded = kb.upload(project, "Uploaded text.", "uploaded.txt")
    kb.indexer.run_pending()  # both uploaded and processing, polls queued
    # A restart loses the queue; pretend one document never reached RAGFlow.
    stuck = kb.repo.get_document(project.id, never_uploaded.id)
    stuck.embedding_status, stuck.remote_doc_id = "pending", None
    kb.repo.save_document(stuck)
    remote_before = kb.repo.get_document(project.id, uploaded.id).remote_doc_id
    restarted = KnowledgeIndexer(retriever=kb.retriever, repository=kb.repo, settings=kb.settings, clock=kb.clock)

    assert restarted.sweep() == 2
    restarted.run_pending()
    kb.clock.advance(5)
    restarted.run_pending()

    assert kb.repo.get_document(project.id, never_uploaded.id).embedding_status == "completed"
    after = kb.repo.get_document(project.id, uploaded.id)
    assert after.embedding_status == "completed"
    assert after.remote_doc_id == remote_before  # polled again, not uploaded twice


def test_startup_sweep_does_nothing_while_rag_is_disabled(kb):
    # covers: AC-8
    service = KnowledgeService(repository=kb.repo, retriever=NullRetriever(), indexer=kb.indexer, settings=kb.settings)
    project = kb.project()
    doc = service.upload(project, io.BytesIO(b"hello"), "a.txt", "text/plain")
    disabled = KnowledgeIndexer(retriever=NullRetriever(), repository=kb.repo, settings=kb.settings, clock=kb.clock)
    assert disabled.sweep() == 0
    stored = kb.repo.get_document(project.id, doc.id)
    assert (stored.embedding_status, stored.error) == ("pending", DISABLED_ERROR)


# ------------------------------------------------------------ AC-5 delete


def test_delete_removes_the_record_file_and_remote_copy(kb):
    # covers: AC-5
    project = kb.project()
    doc = kb.upload(project)
    kb.index_all()
    remote_id = kb.repo.get_document(project.id, doc.id).remote_doc_id
    dataset = kb.retriever.datasets[kb.repo.get_project(project.id).rag_dataset_id]
    assert remote_id in dataset

    assert kb.service.delete(project, doc.id) is True
    assert kb.repo.get_document(project.id, doc.id) is None
    assert not Path(doc.storage_path).parent.exists()
    kb.indexer.run_pending()
    assert remote_id not in dataset


def test_deleting_in_one_project_never_touches_another(kb):
    # covers: AC-5
    a = kb.project("A", "https://a.example.com")
    b = kb.project("B", "https://b.example.com")
    doc_a = kb.upload(a, "Project A spec text.", "spec.txt")
    doc_b = kb.upload(b, "Project B spec text.", "spec.txt")
    kb.index_all()

    assert kb.service.delete(b, doc_a.id) is False  # A's document, asked for from B
    assert kb.service.delete(b, doc_b.id) is True
    kb.indexer.run_pending()

    assert Path(doc_a.storage_path).exists()
    remote_a = kb.repo.get_document(a.id, doc_a.id).remote_doc_id
    assert remote_a in kb.retriever.datasets[kb.repo.get_project(a.id).rag_dataset_id]
    assert kb.service.search(a, "project spec text")


def test_a_failed_remote_delete_still_deletes_locally(kb, caplog):
    # covers: AC-5
    kb.settings.rag.index_retry_delays = []
    project = kb.project()
    doc = kb.upload(project)
    kb.index_all()
    _flaky(kb.retriever, "delete", failures=99)

    assert kb.service.delete(project, doc.id) is True
    with caplog.at_level("WARNING"):
        kb.indexer.run_pending()
    assert kb.repo.get_document(project.id, doc.id) is None
    assert "remote delete failed" in caplog.text


def test_a_remote_delete_refused_while_parsing_is_retried_until_it_lands(kb):
    # covers: AC-5. RAGFlow refuses to delete a document it is still parsing;
    # the retriever reports that as transient, so the indexer must try again.
    kb.settings.rag.index_retry_delays = [10, 60, 300]
    project = kb.project()
    doc = kb.upload(project)
    kb.index_all()
    remote_id = kb.repo.get_document(project.id, doc.id).remote_doc_id
    dataset = kb.retriever.datasets[kb.repo.get_project(project.id).rag_dataset_id]
    calls = _flaky(kb.retriever, "delete", failures=1)

    assert kb.service.delete(project, doc.id) is True
    kb.indexer.run_pending()
    assert remote_id in dataset and kb.indexer.pending() == 1

    kb.clock.advance(10)
    kb.indexer.run_pending()
    assert calls["n"] == 2
    assert remote_id not in dataset
    assert kb.indexer.pending() == 0


def test_delete_during_upload_is_not_undone_by_the_indexer(kb):
    # covers: AC-5
    project = kb.project()
    doc = kb.upload(project)
    original = kb.retriever.upload_file

    def upload_then_delete(proj, path, name):
        result = original(proj, path, name)
        kb.service.delete(project, doc.id)
        return result

    kb.retriever.upload_file = upload_then_delete
    kb.indexer.run_pending()

    assert kb.repo.get_document(project.id, doc.id) is None
    assert kb.retriever.datasets[kb.repo.get_project(project.id).rag_dataset_id] == {}


def test_delete_never_removes_a_path_outside_the_document_folder(kb, tmp_path):
    # covers: AC-5
    project = kb.project()
    outside = tmp_path / "keep" / "important.txt"
    outside.parent.mkdir()
    outside.write_text("keep me")
    legacy = _legacy_document(project.id, str(outside))
    kb.repo.save_document(legacy)
    assert kb.service.delete(project, legacy.id) is True
    assert outside.read_text() == "keep me"


# ------------------------------------------------------------ AC-7 status


def test_status_reports_counts_dataset_and_last_indexed(kb):
    # covers: AC-7
    project = kb.project()
    assert kb.service.status(project) == {
        "enabled": True, "dataset_id": None, "documents": {}, "entries": {}, "last_indexed_at": None,
        "pending_deletes": {"count": 0, "oldest_created_at": None, "last_error": None},
    }

    first = kb.upload(project)
    kb.index_all()
    kb.upload(project, "Second.", "two.txt")
    status = kb.service.status(project)
    assert status["documents"] == {"completed": 1, "pending": 1}
    assert status["dataset_id"] == kb.repo.get_project(project.id).rag_dataset_id
    assert status["last_indexed_at"] == kb.repo.get_document(project.id, first.id).uploaded_at


# ------------------------------------------------------------ AC-9 old documents


def test_old_documents_are_marked_failed_once(kb):
    # covers: AC-9
    project = kb.project()
    legacy = _legacy_document(project.id, "uploads/old.pdf")
    kb.repo.save_document(legacy)
    real = kb.upload(project)

    assert kb.repo.run_knowledge_migration_0006() == 1
    old = kb.repo.get_document(project.id, legacy.id)
    assert (old.embedding_status, old.error) == ("failed", OLD_DOCUMENT_ERROR)
    assert kb.repo.get_document(project.id, real.id).embedding_status == "pending"
    assert kb.repo._read_raw()[KNOWLEDGE_MIGRATION_MARKER] == "done"

    # A second run changes nothing, even for a document that would match.
    another = legacy.model_copy(update={"id": uuid4()})
    kb.repo.save_document(another)
    assert kb.repo.run_knowledge_migration_0006() == 0
    assert kb.repo.get_document(project.id, another.id).embedding_status == "pending"


# ------------------------------------------------------------ AC-10 project cleanup


def test_project_delete_removes_folder_and_dataset(api):
    # covers: AC-10
    project_id = _new_project(api.client)
    api.client.post(f"/api/v1/projects/{project_id}/documents/upload", files={"file": ("a.txt", b"hello there", "text/plain")})
    api.indexer.run_pending()
    from uuid import UUID

    dataset_id = api.repo.get_project(UUID(project_id)).rag_dataset_id
    datasets = api.indexer.retriever.datasets
    folder = Path(main_module.knowledge_service.settings.rag.storage_root) / project_id
    assert folder.exists() and dataset_id in datasets

    assert api.client.delete(f"/api/v1/projects/{project_id}").status_code == 200
    assert not folder.exists()
    api.indexer.run_pending()
    assert dataset_id not in datasets


def test_project_delete_succeeds_when_the_dataset_delete_fails(api, caplog):
    # covers: AC-10
    project_id = _new_project(api.client)
    api.client.post(f"/api/v1/projects/{project_id}/documents/upload", files={"file": ("a.txt", b"hello", "text/plain")})
    api.indexer.run_pending()
    _flaky(api.indexer.retriever, "delete_dataset", failures=99, transient=False)

    assert api.client.delete(f"/api/v1/projects/{project_id}").status_code == 200
    with caplog.at_level("WARNING"):
        api.indexer.run_pending()
    assert "remote delete failed, kept for the next resweep" in caplog.text
    # Spec 0007, AC-5: the row stays, with the attempt recorded.
    [row] = api.indexer.repo.list_remote_deletes(UUID(project_id))
    assert (row.kind, row.reason, row.attempts) == ("dataset", "project_deleted", 1)


# ------------------------------------------------------------ AC-7 route, AC-6


def test_status_route(api):
    # covers: AC-7
    project_id = _new_project(api.client)
    api.client.post(f"/api/v1/projects/{project_id}/documents/upload", files={"file": ("a.txt", b"hello", "text/plain")})
    response = api.client.get(f"/api/v1/projects/{project_id}/knowledge/status")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"enabled", "dataset_id", "documents", "entries", "last_indexed_at", "pending_deletes"}
    assert body["documents"] == {"pending": 1}


def test_an_outsider_cannot_read_status_or_delete(api, roles_db, signed_in):
    # covers: AC-6
    project_id = _new_project(api.client)
    doc = api.client.post(
        f"/api/v1/projects/{project_id}/documents/upload", files={"file": ("a.txt", b"hi", "text/plain")}
    ).json()
    signed_in(_user(roles_db, "Project Manager"))  # has Knowledge delete, but is not on this project
    assert api.client.get(f"/api/v1/projects/{project_id}/knowledge/status").status_code == 403
    assert api.client.delete(f"/api/v1/projects/{project_id}/documents/{doc['id']}").status_code == 403
    assert len(api.repo.get_documents(project_id)) == 1


# ------------------------------------------------------------ settings key (spec 0006 invariants)


def test_settings_never_return_the_ragflow_key(api):
    rag = api.client.get("/api/v1/settings").json()["rag"]
    assert "ragflow_api_key" not in rag and "ragflow_dataset_id" not in rag
    assert isinstance(rag["ragflow_api_key_set"], bool)


def test_saved_rag_settings_drop_the_key_and_the_dataset(monkeypatch):
    monkeypatch.delenv("RAGFLOW_API_KEY", raising=False)
    kept = knowledge_settings_overrides({"enabled": True, "ragflow_api_key": "", "ragflow_dataset_id": "old"})
    assert kept == {"enabled": True}
    assert "RAGFLOW_API_KEY" not in os.environ  # empty keeps the current key

    typed = knowledge_settings_overrides({"enabled": True, "ragflow_api_key": " new-key "})
    assert typed == {"enabled": True}  # never written to override.yaml
    assert os.environ["RAGFLOW_API_KEY"] == "new-key"
