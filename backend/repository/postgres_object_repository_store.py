import logging
from typing import Callable, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.database.db import SessionLocal
from backend.database.db_models import (
    ObjectElementDB,
    ObjectLocatorDB,
    ObjectPageDB,
    ProjectDB,
    RequirementDB,
    ScenarioDB,
    TestCaseDB,
)
from backend.models.object_repository import (
    ObjectElement,
    ObjectLocator,
    ObjectPage,
    ProjectObjectRepository,
)
from backend.repository.object_repository_store import (
    ObjectRepositoryIntegrityError,
    ObjectRepositoryStore,
    ObjectRepositoryUnit,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

_PAGE_FIELDS = (
    "project_id", "name", "url_pattern", "last_scanned_at", "created_at", "updated_at",
    "is_deleted", "deleted_at", "deleted_by",
)
_ELEMENT_FIELDS = (
    "page_id", "name", "element_type", "description", "fingerprint", "health",
    "manually_edited", "last_used_at", "last_execution_id", "last_locator_index",
    "failure_count", "consecutive_failed_runs", "applied_execution_ids",
    "created_at", "updated_at", "is_deleted", "deleted_at", "deleted_by",
)
_LOCATOR_FIELDS = (
    "position", "strategy", "value", "options", "success_count", "failure_count",
    "last_succeeded_at", "created_at", "updated_at",
)


def _values(model, fields: tuple[str, ...]) -> dict:
    return {field: getattr(model, field) for field in fields}


class PostgresObjectRepositoryStore(ObjectRepositoryStore):
    """Uses a short lived session per call. Every failure rolls back and closes it."""

    def _load(self, session, project_id: UUID) -> ProjectObjectRepository:
        pages = session.scalars(
            select(ObjectPageDB)
            .where(ObjectPageDB.project_id == project_id)
            .order_by(ObjectPageDB.created_at)
        ).all()
        page_ids = [page.id for page in pages]
        elements = session.scalars(
            select(ObjectElementDB)
            .where(ObjectElementDB.page_id.in_(page_ids))
            .order_by(ObjectElementDB.created_at)
        ).all() if page_ids else []
        element_ids = [element.id for element in elements]
        locators = session.scalars(
            select(ObjectLocatorDB)
            .where(ObjectLocatorDB.element_id.in_(element_ids))
            .order_by(ObjectLocatorDB.position)
        ).all() if element_ids else []

        locators_by_element: dict[UUID, list[ObjectLocator]] = {}
        for row in locators:
            locators_by_element.setdefault(row.element_id, []).append(
                ObjectLocator(id=row.id, **_values(row, _LOCATOR_FIELDS))
            )
        elements_by_page: dict[UUID, list[ObjectElement]] = {}
        for row in elements:
            values = _values(row, _ELEMENT_FIELDS)
            values["applied_execution_ids"] = list(values["applied_execution_ids"] or [])
            elements_by_page.setdefault(row.page_id, []).append(
                ObjectElement(id=row.id, locators=locators_by_element.get(row.id, []), **values)
            )
        return ProjectObjectRepository(
            project_id=project_id,
            pages=[
                ObjectPage(id=row.id, elements=elements_by_page.get(row.id, []), **_values(row, _PAGE_FIELDS))
                for row in pages
            ],
        )

    def load(self, project_id: UUID) -> ProjectObjectRepository:
        session = SessionLocal()
        try:
            return self._load(session, project_id)
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _project_scripts(session, project_id: UUID) -> dict[UUID, str]:
        rows = session.execute(
            select(TestCaseDB.id, TestCaseDB.playwright_script)
            .join(ScenarioDB, ScenarioDB.id == TestCaseDB.scenario_id)
            .join(RequirementDB, RequirementDB.id == ScenarioDB.requirement_id)
            .where(
                RequirementDB.project_id == project_id,
                TestCaseDB.is_deleted.is_(False),
                ScenarioDB.is_deleted.is_(False),
            )
        ).all()
        return {row.id: row.playwright_script or "" for row in rows}

    def mutate(self, project_id: UUID, change: Callable[[ObjectRepositoryUnit], T]) -> T:
        session = SessionLocal()
        try:
            # Serializes recon and tester writes for the project, so a write
            # always sees the latest manually_edited value before changing a row.
            session.execute(
                select(ProjectDB.id).where(ProjectDB.id == project_id).with_for_update()
            )
            repository = self._load(session, project_id)
            before = repository.model_copy(deep=True)
            unit = ObjectRepositoryUnit(repository, lambda: self._project_scripts(session, project_id))
            result = change(unit)
            self._save(session, before, repository)
            for test_case_id, script in unit.script_updates.items():
                row = session.get(TestCaseDB, test_case_id)
                if row is not None:
                    row.playwright_script = script
            session.commit()
            return result
        except IntegrityError as exc:
            session.rollback()
            raise ObjectRepositoryIntegrityError(str(exc.orig)) from exc
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _save(self, session, before: ProjectObjectRepository, after: ProjectObjectRepository) -> None:
        old_pages = {page.id: page for page in before.pages}
        old_elements = {element.id: element for page in before.pages for element in page.elements}

        for page in after.pages:
            values = _values(page, _PAGE_FIELDS)
            old = old_pages.get(page.id)
            if old is None:
                session.add(ObjectPageDB(id=page.id, **values))
            elif values != _values(old, _PAGE_FIELDS):
                row = session.get(ObjectPageDB, page.id)
                for field, value in values.items():
                    setattr(row, field, value)
        session.flush()

        changed_locators: list[ObjectElement] = []
        for page in after.pages:
            for element in page.elements:
                values = _values(element, _ELEMENT_FIELDS)
                old = old_elements.get(element.id)
                if old is None:
                    session.add(ObjectElementDB(id=element.id, **values))
                    changed_locators.append(element)
                    continue
                if values != _values(old, _ELEMENT_FIELDS):
                    row = session.get(ObjectElementDB, element.id)
                    for field, value in values.items():
                        setattr(row, field, value)
                if [loc.model_dump() for loc in element.locators] != [loc.model_dump() for loc in old.locators]:
                    changed_locators.append(element)
        session.flush()

        for element in changed_locators:
            rows = {
                row.id: row for row in session.scalars(
                    select(ObjectLocatorDB).where(ObjectLocatorDB.element_id == element.id)
                ).all()
            }
            keep = {locator.id for locator in element.locators}
            for locator_id, row in rows.items():
                if locator_id not in keep:
                    session.delete(row)
                else:
                    # Move kept rows out of the way so a reorder never trips
                    # the (element_id, position) unique constraint mid flush.
                    row.position = row.position + 100
            session.flush()
            for locator in element.locators:
                values = _values(locator, _LOCATOR_FIELDS)
                row = rows.get(locator.id)
                if row is None:
                    session.add(ObjectLocatorDB(id=locator.id, element_id=element.id, **values))
                else:
                    for field, value in values.items():
                        setattr(row, field, value)
            session.flush()
