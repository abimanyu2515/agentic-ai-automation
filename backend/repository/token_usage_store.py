"""
Persistence for LLM token usage records (Token Utilization feature).

Usage is high volume and append only, so the JSON provider writes its own
JSON Lines file instead of rewriting project_store.json on every LLM call.
The PostgreSQL provider writes the token_usage table.
"""

import json
import logging
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from backend.models.token_usage import TokenUsageFilters, TokenUsageRecord

logger = logging.getLogger(__name__)

DEFAULT_JSONL_PATH = Path(__file__).resolve().parent.parent / "database" / "token_usage.jsonl"


class TokenUsageStore(ABC):
    @abstractmethod
    def add_many(self, records: list[TokenUsageRecord]) -> None:
        pass

    @abstractmethod
    def query(self, filters: TokenUsageFilters) -> list[TokenUsageRecord]:
        """Records matching filters, oldest first."""


class InMemoryTokenUsageStore(TokenUsageStore):
    """Used by tests and as a last resort when no store can be built."""

    def __init__(self):
        self._records: list[TokenUsageRecord] = []
        self._lock = threading.Lock()

    def add_many(self, records: list[TokenUsageRecord]) -> None:
        with self._lock:
            self._records.extend(records)

    def query(self, filters: TokenUsageFilters) -> list[TokenUsageRecord]:
        with self._lock:
            matched = [r for r in self._records if filters.matches(r)]
        return sorted(matched, key=lambda r: r.created_at)


class JSONTokenUsageStore(TokenUsageStore):
    """Append only JSON Lines file. A corrupt line is skipped, never fatal."""

    _lock = threading.RLock()

    def __init__(self, path: Path = DEFAULT_JSONL_PATH):
        self.path = Path(path)

    def add_many(self, records: list[TokenUsageRecord]) -> None:
        if not records:
            return
        lines = "".join(record.model_dump_json() + "\n" for record in records)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(lines)

    def query(self, filters: TokenUsageFilters) -> list[TokenUsageRecord]:
        with self._lock:
            if not self.path.exists():
                return []
            with open(self.path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        records: list[TokenUsageRecord] = []
        for number, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = TokenUsageRecord.model_validate(json.loads(line))
            except Exception:
                logger.warning("Skipping unreadable token usage line %s in %s", number, self.path)
                continue
            if filters.matches(record):
                records.append(record)
        return sorted(records, key=lambda r: r.created_at)


def get_token_usage_store(project_repo=None) -> TokenUsageStore:
    """The store matching the active project repository provider."""
    if project_repo is None:
        from backend.repository.project_repository import get_project_repository

        project_repo = get_project_repository()
    try:
        from backend.repository.postgres_project_repository import PostgresProjectRepository
    except Exception:
        PostgresProjectRepository = None  # type: ignore[assignment]
    if PostgresProjectRepository is not None and isinstance(project_repo, PostgresProjectRepository):
        from backend.repository.postgres_token_usage_store import PostgresTokenUsageStore

        return PostgresTokenUsageStore()
    return JSONTokenUsageStore()
