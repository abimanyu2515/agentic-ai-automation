"""
Persistence for the object repository (spec 0004).

Each store loads and saves one project's whole repository as a
`ProjectObjectRepository` aggregate. `mutate` runs a change function inside
one atomic unit:

* JSON store: one `_read_raw` -> change -> `_write_raw` cycle under the
  project store's file lock, so nothing interleaves.
* PostgreSQL store: one transaction that locks the project row
  (`SELECT ... FOR UPDATE`) before loading, then writes the changed rows.

Test case scripts of the project are available inside the unit, so a rename
can rewrite references in the same transaction as the rename itself.
"""

from abc import ABC, abstractmethod
from typing import Callable, TypeVar
from uuid import UUID

from backend.models.object_repository import ProjectObjectRepository

T = TypeVar("T")


class ObjectRepositoryIntegrityError(Exception):
    """A unique index rejected the write (a duplicate name, pattern, or fingerprint)."""


class ObjectRepositoryUnit:
    """The state a `mutate` change function works on."""

    def __init__(self, repository: ProjectObjectRepository, load_scripts: Callable[[], dict[UUID, str]]):
        self.repository = repository
        self._load_scripts = load_scripts
        self._scripts: dict[UUID, str] | None = None
        self.script_updates: dict[UUID, str] = {}

    def test_case_scripts(self) -> dict[UUID, str]:
        """Stored scripts of the project's non deleted test cases, by test case ID."""
        if self._scripts is None:
            self._scripts = self._load_scripts()
        return {**self._scripts, **self.script_updates}

    def set_test_case_script(self, test_case_id: UUID, script: str) -> None:
        self.script_updates[test_case_id] = script


class ObjectRepositoryStore(ABC):
    @abstractmethod
    def load(self, project_id: UUID) -> ProjectObjectRepository:
        pass

    @abstractmethod
    def mutate(self, project_id: UUID, change: Callable[[ObjectRepositoryUnit], T]) -> T:
        """Run `change` atomically and persist the aggregate and script updates."""


class JSONObjectRepositoryStore(ObjectRepositoryStore):
    """Mirrors the repository as nested lists under each project in project_store.json."""

    def __init__(self, project_repo):
        self.project_repo = project_repo

    def _aggregate(self, raw: dict, project_id: UUID) -> ProjectObjectRepository:
        stored = raw.get("object_repository", {}).get(str(project_id))
        if not stored:
            return ProjectObjectRepository(project_id=project_id)
        return ProjectObjectRepository.model_validate({"project_id": str(project_id), "pages": stored.get("pages", [])})

    @staticmethod
    def _project_test_case_ids(raw: dict, project_id: UUID) -> set[str]:
        project = next(
            (p for p in raw.get("projects", []) if p.get("id") == str(project_id)),
            None,
        )
        if not project:
            return set()
        requirement_ids = {str(r) for r in project.get("requirements", [])}
        scenario_ids = {
            s_id for s_id, scenario in raw.get("scenarios", {}).items()
            if scenario.get("requirement_id") in requirement_ids and not scenario.get("is_deleted", False)
        }
        return {
            tc_id for tc_id, test_case in raw.get("test_cases", {}).items()
            if test_case.get("scenario_id") in scenario_ids and not test_case.get("is_deleted", False)
        }

    def load(self, project_id: UUID) -> ProjectObjectRepository:
        return self._aggregate(self.project_repo._read_raw(), project_id)

    def mutate(self, project_id: UUID, change: Callable[[ObjectRepositoryUnit], T]) -> T:
        with self.project_repo._write_lock:
            raw = self.project_repo._read_raw()
            repository = self._aggregate(raw, project_id)
            before = repository.model_dump(mode="json")

            def load_scripts() -> dict[UUID, str]:
                test_cases = raw.get("test_cases", {})
                return {
                    UUID(tc_id): test_cases[tc_id].get("playwright_script") or ""
                    for tc_id in self._project_test_case_ids(raw, project_id)
                }

            unit = ObjectRepositoryUnit(repository, load_scripts)
            result = change(unit)

            after = repository.model_dump(mode="json")
            if after == before and not unit.script_updates:
                return result
            raw.setdefault("object_repository", {})[str(project_id)] = {"pages": after["pages"]}
            test_cases = raw.setdefault("test_cases", {})
            for test_case_id, script in unit.script_updates.items():
                if str(test_case_id) in test_cases:
                    test_cases[str(test_case_id)]["playwright_script"] = script
            self.project_repo._write_raw(raw)
            return result


def get_object_repository_store(project_repo) -> ObjectRepositoryStore | None:
    """The store matching the active project repository, or None for test doubles."""
    from backend.repository.project_repository import JSONProjectRepository

    if isinstance(project_repo, JSONProjectRepository):
        return JSONObjectRepositoryStore(project_repo)
    try:
        from backend.repository.postgres_project_repository import PostgresProjectRepository
    except Exception:
        return None
    if isinstance(project_repo, PostgresProjectRepository):
        from backend.repository.postgres_object_repository_store import PostgresObjectRepositoryStore

        return PostgresObjectRepositoryStore()
    return None
