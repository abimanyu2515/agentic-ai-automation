import pytest
from sqlalchemy.exc import SQLAlchemyError

import backend.repository.postgres_project_repository as postgres_repository
from backend.repository.postgres_project_repository import PostgresProjectRepository


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
