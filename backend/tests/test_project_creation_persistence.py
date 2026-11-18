"""Spec 0002: rollback, explicit server errors, log redaction, and PostgreSQL URL conflicts.

Covers AC-5, AC-8, and AC-9. The PostgreSQL tests run only when the configured
engine is PostgreSQL and reachable; they create a throwaway user and remove every
row they write.
"""

import logging
import os
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from backend.main import app
from backend.repository.project_repository import JSONProjectRepository, ProjectURLConflictError
from backend.repository.postgres_project_repository import _is_target_url_conflict
from backend.services.project_configuration import redact_url_for_log


@pytest.fixture
def json_repo(tmp_path):
    import backend.repository.project_repository as repo_module

    previous = repo_module._active_project_repo
    repo = JSONProjectRepository(tmp_path / "store.json")
    repo_module._active_project_repo = repo
    yield repo
    repo_module._active_project_repo = previous


@pytest.fixture
def client(json_repo):
    return TestClient(app)


def _payload(**overrides):
    return {
        "name": "Persistence Project",
        "description": "Spec 0002 persistence coverage",
        "framework": "playwright",
        "target_url": "https://app.example.com/login?token=s3cr3t",
        **overrides,
    }


# AC-9: logs never carry query text, fragments, or userinfo.

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://app.example.com/login?token=s3cr3t", "https://app.example.com/login?<redacted>"),
        ("https://user:pw@App.Example.com:8443/x#frag", "https://app.example.com:8443/x"),
        ("http://example.com", "http://example.com"),
        ("not a url", "<invalid url>"),
        (None, None),
    ],
)
def test_redact_url_for_log_keeps_only_scheme_host_port_and_path(raw, expected):
    assert redact_url_for_log(raw) == expected


def test_successful_create_logs_project_context_without_query_text(client, caplog):
    with caplog.at_level(logging.INFO, logger="backend.project_creation"):
        response = client.post("/api/v1/projects", json=_payload())

    assert response.status_code == 201
    assert "s3cr3t" not in caplog.text
    assert "Project created" in caplog.text
    assert "https://app.example.com/login?<redacted>" in caplog.text


def test_rejected_create_logs_field_names_only(client, caplog):
    with caplog.at_level(logging.INFO, logger="backend.project_creation"):
        response = client.post(
            "/api/v1/projects",
            json=_payload(target_url="https://user:hunter2@example.com/"),
        )

    assert response.status_code == 422
    assert "hunter2" not in caplog.text
    assert "target_url" in caplog.text


# AC-8: an unexpected persistence failure is an explicit 500 and leaves nothing behind.

def test_json_write_failure_returns_explicit_500_and_persists_nothing(client, json_repo, monkeypatch, caplog):
    def failing_replace(*_args, **_kwargs):
        raise OSError("disk full at https://app.example.com/login?token=s3cr3t")

    monkeypatch.setattr(os, "replace", failing_replace)
    with caplog.at_level(logging.INFO, logger="backend.project_creation"):
        response = client.post("/api/v1/projects", json=_payload())
    monkeypatch.undo()

    assert response.status_code == 500
    assert response.json()["code"] == "PROJECT_PERSISTENCE_FAILED"
    assert json_repo.list_projects() == []
    assert not json_repo.file_path.with_suffix(".tmp").exists()
    assert "s3cr3t" not in caplog.text
    assert "OSError" in caplog.text


def test_create_failure_never_writes_the_vault(client, monkeypatch):
    from backend.services import vault_service

    calls = []
    monkeypatch.setattr(vault_service.VaultService, "set_credentials", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")))

    response = client.post("/api/v1/projects", json=_payload())

    assert response.status_code == 500
    assert calls == []


# AC-5 / AC-8: only the owner scoped URL index maps to a URL conflict.

def _integrity_error(constraint_name=None, message=""):
    orig = SimpleNamespace(diag=SimpleNamespace(constraint_name=constraint_name))
    orig.__str__ = lambda self=orig: message
    return IntegrityError("INSERT", {}, orig)


def test_url_index_violation_is_a_url_conflict():
    assert _is_target_url_conflict(_integrity_error("uq_projects_owner_target_url"))


def test_name_constraint_violation_is_not_a_url_conflict():
    assert not _is_target_url_conflict(_integrity_error("uq_projects_owner_name"))


def test_constraint_name_falls_back_to_the_error_message():
    class Orig(Exception):
        pass

    exc = IntegrityError("INSERT", {}, Orig('duplicate key value violates unique constraint "uq_projects_owner_target_url"'))
    assert _is_target_url_conflict(exc)


# Live PostgreSQL: concurrent duplicates and rollback with session cleanup.

def _postgres_engine():
    try:
        from backend.database.db import engine
    except Exception:
        return None
    if engine.dialect.name != "postgresql":
        return None
    try:
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        return None
    return engine


@pytest.fixture
def pg_owner():
    engine = _postgres_engine()
    if engine is None:
        pytest.skip("PostgreSQL is not the configured, reachable database")
    from backend.repository.postgres_project_repository import PostgresProjectRepository, db_session

    owner_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users (id, email, password_hash, is_active, created_at, updated_at) "
                "VALUES (:id, :email, 'x', true, now(), now())"
            ),
            {"id": owner_id, "email": f"spec0002-{owner_id}@test.invalid"},
        )
    yield PostgresProjectRepository(), owner_id
    db_session.remove()
    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM projects WHERE owner_id = :id"), {"id": owner_id})
        connection.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": owner_id})


def _owner_projects(owner_id):
    from backend.database.db import engine

    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT id, target_url FROM projects WHERE owner_id = :id"), {"id": owner_id}
        ).all()


def test_postgres_concurrent_duplicates_yield_one_project_and_one_conflict(pg_owner):
    from backend.repository.postgres_project_repository import db_session

    repo, owner_id = pg_owner
    barrier = threading.Barrier(2)
    results = []

    def create(name):
        barrier.wait()
        try:
            project = repo.create_project(
                name, "race", framework="playwright",
                target_url="https://race.example.com/", owner_id=owner_id, created_by=owner_id,
            )
            results.append(("ok", project.id))
        except ProjectURLConflictError as exc:
            results.append(("conflict", exc.conflicting_project_id))
        finally:
            db_session.remove()

    threads = [threading.Thread(target=create, args=(f"Racer {i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    outcomes = sorted(kind for kind, _ in results)
    assert outcomes == ["conflict", "ok"]
    winner = next(value for kind, value in results if kind == "ok")
    loser_reported = next(value for kind, value in results if kind == "conflict")
    assert loser_reported == winner
    assert [row.id for row in _owner_projects(owner_id)] == [winner]


def test_postgres_non_url_integrity_error_rolls_back_and_session_stays_usable(pg_owner):
    repo, owner_id = pg_owner
    repo.create_project(
        "Same Name", "first", framework="playwright",
        target_url="https://one.example.com", owner_id=owner_id, created_by=owner_id,
    )

    with pytest.raises(IntegrityError):
        repo.create_project(
            "Same Name", "second", framework="playwright",
            target_url="https://two.example.com", owner_id=owner_id, created_by=owner_id,
        )

    # No partial row, and the scoped session is clean for the next operation.
    assert [row.target_url for row in _owner_projects(owner_id)] == ["https://one.example.com"]
    third = repo.create_project(
        "Another Name", "third", framework="playwright",
        target_url="https://three.example.com", owner_id=owner_id, created_by=owner_id,
    )
    assert third.target_url == "https://three.example.com"


# AC-10: the create modal markup carries labels, required state, and announced errors.
# A keyboard walkthrough in a real browser stays a /check verify step (no e2e tool is configured).

def test_create_project_modal_fields_are_labelled_required_and_announce_errors():
    from html.parser import HTMLParser
    from pathlib import Path

    class Collector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.elements = {}
            self.labels = set()

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "label" and attrs.get("for"):
                self.labels.add(attrs["for"])
            if attrs.get("id"):
                self.elements[attrs["id"]] = (tag, attrs)

    html = Path(__file__).resolve().parents[2] / "frontend" / "index.html"
    collector = Collector()
    collector.feed(html.read_text(encoding="utf-8"))

    for field_id, error_id in [
        ("new-proj-target-url", "new-proj-target-url-error"),
        ("new-proj-desc", "new-proj-desc-error"),
    ]:
        _tag, attrs = collector.elements[field_id]
        assert field_id in collector.labels
        assert "required" in attrs
        assert attrs.get("aria-describedby") == error_id
        assert collector.elements[error_id][1].get("role") == "alert"

    assert collector.elements["new-proj-target-url"][1].get("type") == "url"
    assert "new-proj-name" in collector.labels
