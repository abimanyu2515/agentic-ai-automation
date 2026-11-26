"""
Script heal persistence (spec 0011, AC-17, AC-21).

`ScriptHealStore` is the interface. `InMemoryScriptHealStore` keeps heals in a
dict (tests). `JsonScriptHealStore` stores them in
`backend/database/script_heal_store.json` under one process lock and writes
through a temp file plus an atomic rename. `PostgresScriptHealStore`
(postgres_script_heal_store.py) uses the `test_case_script_heals` table. The
active store follows the same provider setting as `get_project_repository()`.

Every change to a test case's heals goes through `mutate(test_case_id, fn)`, so
the single suggestion rule and retention are applied in one read and write.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, TypeVar
from uuid import UUID

from backend.models.script_heal import ScriptHeal

SCRIPT_HEAL_STORE_PATH = Path(__file__).parent.parent / "database" / "script_heal_store.json"

T = TypeVar("T")


class ScriptHealIntegrityError(Exception):
    """Another run saved a suggestion for the same test case first (PostgreSQL partial unique index)."""


class ScriptHealStoreCorrupt(Exception):
    """The JSON store file exists but can't be read or parsed. Raised instead of reading
    it as empty, so the next write never overwrites the heals it still holds."""


class ScriptHealStore(ABC):
    @abstractmethod
    def list_heals(self, project_id: UUID, status: str | None = None) -> list[ScriptHeal]:
        """Newest first."""

    @abstractmethod
    def list_for_test_case(self, test_case_id: UUID) -> list[ScriptHeal]:
        """Oldest first."""

    @abstractmethod
    def get(self, heal_id: UUID) -> ScriptHeal | None:
        pass

    @abstractmethod
    def mutate(self, test_case_id: UUID, change: Callable[[list[ScriptHeal]], T]) -> T:
        """Run `change` on the test case's heals (a mutable list, oldest first) and save
        the list it leaves: added heals are inserted, changed ones updated, removed ones
        deleted. An exception inside `change` saves nothing."""

    def latest_for_test_case(self, test_case_id: UUID) -> ScriptHeal | None:
        heals = self.list_for_test_case(test_case_id)
        return max(heals, key=lambda heal: heal.created_at, default=None)


class InMemoryScriptHealStore(ScriptHealStore):
    def __init__(self):
        self._heals: dict[UUID, ScriptHeal] = {}
        self._lock = threading.RLock()

    def _read(self) -> dict[UUID, ScriptHeal]:
        return {k: v.model_copy(deep=True) for k, v in self._heals.items()}

    def _write(self, heals: dict[UUID, ScriptHeal]) -> None:
        self._heals = {k: v.model_copy(deep=True) for k, v in heals.items()}

    def list_heals(self, project_id: UUID, status: str | None = None) -> list[ScriptHeal]:
        with self._lock:
            rows = [
                heal for heal in self._read().values()
                if heal.project_id == project_id and (status is None or heal.status == status)
            ]
        return sorted(rows, key=lambda heal: heal.created_at, reverse=True)

    def list_for_test_case(self, test_case_id: UUID) -> list[ScriptHeal]:
        with self._lock:
            rows = [heal for heal in self._read().values() if heal.test_case_id == test_case_id]
        return sorted(rows, key=lambda heal: heal.created_at)

    def get(self, heal_id: UUID) -> ScriptHeal | None:
        with self._lock:
            return self._read().get(heal_id)

    def mutate(self, test_case_id: UUID, change: Callable[[list[ScriptHeal]], T]) -> T:
        with self._lock:
            everything = self._read()
            mine = sorted(
                (heal for heal in everything.values() if heal.test_case_id == test_case_id),
                key=lambda heal: heal.created_at,
            )
            result = change(mine)
            for heal_id in [k for k, v in everything.items() if v.test_case_id == test_case_id]:
                del everything[heal_id]
            for heal in mine:
                everything[heal.id] = heal
            self._write(everything)
            return result


class JsonScriptHealStore(InMemoryScriptHealStore):
    _file_lock = threading.RLock()

    def __init__(self, path: Path | None = None):
        super().__init__()
        self.path = Path(path or SCRIPT_HEAL_STORE_PATH)
        self._lock = self._file_lock

    def _read(self) -> dict[UUID, ScriptHeal]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            return {UUID(k): ScriptHeal.model_validate(v) for k, v in raw.get("script_heals", {}).items()}
        except (OSError, ValueError, AttributeError) as exc:
            # json.JSONDecodeError and pydantic's ValidationError are ValueErrors.
            raise ScriptHealStoreCorrupt(f"Could not read {self.path.name}: {type(exc).__name__}") from exc

    def _write(self, heals: dict[UUID, ScriptHeal]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"script_heals": {str(k): v.model_dump(mode="json") for k, v in heals.items()}}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".script_heals_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


_store: ScriptHealStore | None = None
_store_lock = threading.Lock()


def get_script_heal_store() -> ScriptHealStore:
    """The script heal store for the active repository provider (json or postgres)."""
    global _store
    with _store_lock:
        if _store is None:
            from backend.config.settings import get_settings

            if get_settings().repository.provider == "postgres":
                from backend.repository.postgres_script_heal_store import PostgresScriptHealStore

                _store = PostgresScriptHealStore()
            else:
                _store = JsonScriptHealStore()
        return _store


def set_script_heal_store(store: ScriptHealStore | None) -> None:
    """Swap the active store. Tests use this; None goes back to the provider default."""
    global _store
    with _store_lock:
        _store = store
