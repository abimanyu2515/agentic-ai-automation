"""The ORM schema must build on SQLite, which the JSON provider uses as its local fallback database."""
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from backend.database.db import Base
import backend.database.db_models  # noqa: F401  registers every table on Base.metadata


def test_full_schema_creates_on_fresh_sqlite():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    Base.metadata.create_all(engine)

    indexes = {index["name"] for index in inspect(engine).get_indexes("projects")}
    assert "uq_projects_owner_target_url" in indexes
