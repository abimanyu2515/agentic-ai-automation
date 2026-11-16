from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import scoped_session, sessionmaker

import backend.repository.postgres_project_repository as postgres_repository
from backend.database.db import Base
from backend.database.db_models import ProjectDB, RequirementDB
from backend.repository.postgres_project_repository import PostgresProjectRepository


@pytest.fixture
def sqlite_repo(tmp_path, monkeypatch):
    """The real repository on a throwaway SQLite file, with only the tables it needs.
    PostgreSQL partial indexes do not compile on SQLite and are not under test here."""
    engine = create_engine(f"sqlite:///{tmp_path / 'repo.db'}")
    tables = [Base.metadata.tables[name] for name in ("users", "projects", "releases", "requirements")]
    saved_indexes = {table.name: set(table.indexes) for table in tables}
    for table in tables:
        table.indexes.clear()
    try:
        Base.metadata.create_all(engine, tables=tables)
    finally:
        for table in tables:
            table.indexes.update(saved_indexes[table.name])
    monkeypatch.setattr(postgres_repository, "db_session", scoped_session(sessionmaker(bind=engine)))
    yield PostgresProjectRepository(), sessionmaker(bind=engine)
    engine.dispose()


def test_save_requirement_update_is_committed(sqlite_repo):
    # Regression: the update branch never committed, so a chat edit card
    # reported success while the stored priority stayed the same.
    repo, fresh_session = sqlite_repo
    project_id, requirement_id = uuid4(), uuid4()
    with fresh_session() as session:
        session.add(ProjectDB(id=project_id, name="p", description="d"))
        session.add(RequirementDB(id=requirement_id, project_id=project_id, title="User login",
                                  description="d", priority="high", business_domain="auth"))
        session.commit()

    requirement = repo.get_requirement(requirement_id)
    updated = requirement.model_copy(update={"priority": "low", "business_domain": "payments"})
    repo.save_requirement(updated, updated.priority, updated.business_domain)

    with fresh_session() as other:  # what the next request reads
        stored = other.scalar(select(RequirementDB).where(RequirementDB.id == requirement_id))
        assert (stored.priority, stored.business_domain) == ("low", "payments")


def test_failed_project_query_rolls_back_and_removes_session(monkeypatch):
    class FailedResult:
        def all(self):
            raise SQLAlchemyError("database query failed")

    class SuccessfulResult:
        def all(self):
            return []

    class FakeSession:
        def __init__(self, result):
            self.result = result
            self.rolled_back = False

        def scalars(self, statement):
            return self.result

        def in_transaction(self):
            return not self.rolled_back

        def rollback(self):
            self.rolled_back = True

    failed_session = FakeSession(FailedResult())
    healthy_session = FakeSession(SuccessfulResult())

    class FakeScopedSession:
        def __init__(self):
            self.current_session = failed_session
            self.remove_count = 0

        def __call__(self):
            return self.current_session

        def remove(self):
            self.remove_count += 1
            self.current_session = healthy_session

    scoped_session = FakeScopedSession()
    monkeypatch.setattr(postgres_repository, "db_session", scoped_session)
    repo = PostgresProjectRepository()

    with pytest.raises(SQLAlchemyError, match="database query failed"):
        repo.list_projects()

    assert failed_session.rolled_back
    assert scoped_session.remove_count == 1
    assert repo.list_projects() == []
