import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.database.db import SessionLocal
from backend.database.db_models import TokenUsageDB
from backend.models.token_usage import TokenUsageFilters, TokenUsageRecord
from backend.repository.token_usage_store import TokenUsageStore

logger = logging.getLogger(__name__)

_FIELDS = tuple(TokenUsageRecord.model_fields)


class PostgresTokenUsageStore(TokenUsageStore):
    """Uses a short lived session per call. Every failure rolls back and closes it."""

    def add_many(self, records: list[TokenUsageRecord]) -> None:
        if not records:
            return
        session = SessionLocal()
        try:
            session.add_all(
                TokenUsageDB(**{field: getattr(record, field) for field in _FIELDS})
                for record in records
            )
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            session.close()

    def query(self, filters: TokenUsageFilters) -> list[TokenUsageRecord]:
        statement = select(TokenUsageDB)
        if filters.start:
            statement = statement.where(TokenUsageDB.created_at >= filters.start)
        if filters.end:
            statement = statement.where(TokenUsageDB.created_at < filters.end)
        if filters.project_id:
            statement = statement.where(TokenUsageDB.project_id == filters.project_id)
        for field in ("agent", "model", "role", "provider", "status", "operation"):
            value = getattr(filters, field)
            if value:
                statement = statement.where(getattr(TokenUsageDB, field) == value)
        statement = statement.order_by(TokenUsageDB.created_at)

        session = SessionLocal()
        try:
            rows = session.scalars(statement).all()
            return [
                TokenUsageRecord.model_validate({field: getattr(row, field) for field in _FIELDS})
                for row in rows
            ]
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            session.close()
