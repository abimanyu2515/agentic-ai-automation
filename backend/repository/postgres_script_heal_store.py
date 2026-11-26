"""PostgreSQL script heal store (spec 0011, AC-17).

Each call uses its own short lived session. Any failure rolls the session back
and closes it before the error is raised, so a failed transaction is never
reused. An `IntegrityError` on the one suggestion per test case index becomes
`ScriptHealIntegrityError`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.database.db import SessionLocal
from backend.database.db_models import TestCaseScriptHealDB
from backend.models.script_heal import ScriptHeal
from backend.repository.script_heal_store import ScriptHealIntegrityError, ScriptHealStore

T = TypeVar("T")

_FIELDS = (
    "test_case_id", "project_id", "execution_id", "status", "old_script", "new_script",
    "rca_category", "root_cause_summary", "failing_step", "reason", "created_at",
    "decided_by", "decided_at",
)


def _heal(row: TestCaseScriptHealDB) -> ScriptHeal:
    return ScriptHeal(id=row.id, **{name: getattr(row, name) for name in _FIELDS})


class PostgresScriptHealStore(ScriptHealStore):
    def __init__(self, session_factory=None):
        self._session_factory = session_factory or SessionLocal

    @contextmanager
    def _session(self):
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_heals(self, project_id: UUID, status: str | None = None) -> list[ScriptHeal]:
        with self._session() as session:
            stmt = select(TestCaseScriptHealDB).where(TestCaseScriptHealDB.project_id == project_id)
            if status is not None:
                stmt = stmt.where(TestCaseScriptHealDB.status == status)
            stmt = stmt.order_by(TestCaseScriptHealDB.created_at.desc())
            return [_heal(row) for row in session.scalars(stmt).all()]

    def list_for_test_case(self, test_case_id: UUID) -> list[ScriptHeal]:
        with self._session() as session:
            stmt = (
                select(TestCaseScriptHealDB)
                .where(TestCaseScriptHealDB.test_case_id == test_case_id)
                .order_by(TestCaseScriptHealDB.created_at)
            )
            return [_heal(row) for row in session.scalars(stmt).all()]

    def get(self, heal_id: UUID) -> ScriptHeal | None:
        with self._session() as session:
            row = session.get(TestCaseScriptHealDB, heal_id)
            return _heal(row) if row else None

    def mutate(self, test_case_id: UUID, change: Callable[[list[ScriptHeal]], T]) -> T:
        try:
            with self._session() as session:
                stmt = (
                    select(TestCaseScriptHealDB)
                    .where(TestCaseScriptHealDB.test_case_id == test_case_id)
                    .order_by(TestCaseScriptHealDB.created_at)
                    .with_for_update()
                )
                rows = {row.id: row for row in session.scalars(stmt).all()}
                heals = [_heal(row) for row in rows.values()]
                result = change(heals)
                kept = {heal.id for heal in heals}
                # Delete and demote first so a new suggestion never meets the old one in the index.
                for heal_id, row in rows.items():
                    if heal_id not in kept:
                        session.delete(row)
                for heal in heals:
                    row = rows.get(heal.id)
                    if row is not None:
                        for name in _FIELDS:
                            setattr(row, name, getattr(heal, name))
                session.flush()
                for heal in heals:
                    if heal.id not in rows:
                        session.add(TestCaseScriptHealDB(id=heal.id, **{name: getattr(heal, name) for name in _FIELDS}))
                session.flush()
                return result
        except IntegrityError as exc:
            raise ScriptHealIntegrityError(str(exc.orig)) from exc
