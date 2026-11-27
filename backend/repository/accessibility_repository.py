"""
Accessibility report persistence (spec 0011, AC-15).

`AccessibilityReportRepository` is the interface. `InMemoryAccessibilityReportRepository`
keeps reports in a dict (tests). `JsonAccessibilityReportRepository` stores them in
`backend/database/accessibility_report_store.json` under one process lock and writes
through a temp file plus an atomic rename. `PostgresAccessibilityReportRepository`
(postgres_accessibility_repository.py) is the SQL implementation. The active store
follows the same provider setting as `get_project_repository()`.

Reports are kept until their project is deleted; there is no pruning.
"""

import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from uuid import UUID

from backend.models.accessibility import AccessibilityReport, AccessibilityReportStatus, AccessibilitySource

ACCESSIBILITY_STORE_PATH = Path(__file__).parent.parent / "database" / "accessibility_report_store.json"


class AccessibilityReportRepository(ABC):
    @abstractmethod
    def get(self, report_id: UUID) -> AccessibilityReport | None:
        pass

    @abstractmethod
    def get_by_execution(self, execution_id: UUID) -> AccessibilityReport | None:
        """The one report of a run, if any."""

    @abstractmethod
    def save(self, report: AccessibilityReport) -> AccessibilityReport:
        """Insert or replace by id. At most one report per `execution_id`."""

    @abstractmethod
    def list_reports(
        self,
        project_id: UUID,
        source: AccessibilitySource | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[AccessibilityReport], int]:
        """Newest first, plus the total count before paging."""

    @abstractmethod
    def list_by_batch(self, project_id: UUID, batch_id: str) -> list[AccessibilityReport]:
        pass

    @abstractmethod
    def delete_for_project(self, project_id: UUID) -> int:
        """Removes every report of a deleted project. Returns how many were removed."""

    @abstractmethod
    def list_active_scans(self, project_id: UUID | None = None) -> list[AccessibilityReport]:
        """Standalone scans still queued or running, for one project or all of them (AC-10)."""

    def save_new_scan(self, report: AccessibilityReport) -> AccessibilityReport:
        """Inserts a queued scan, refusing a second active scan for the project
        (AC-10). Raises `ActiveScanExists` when one is already queued or running."""
        if self.list_active_scans(report.project_id):
            raise ActiveScanExists(str(report.project_id))
        return self.save(report)


class ActiveScanExists(Exception):
    """A project already has a standalone scan queued or running (AC-10)."""


_ACTIVE_STATUSES = (AccessibilityReportStatus.QUEUED, AccessibilityReportStatus.RUNNING)


class InMemoryAccessibilityReportRepository(AccessibilityReportRepository):
    def __init__(self):
        self._reports: dict[UUID, AccessibilityReport] = {}
        self._lock = threading.RLock()

    def _load(self) -> dict[UUID, AccessibilityReport]:
        return self._reports

    def _store(self, reports: dict[UUID, AccessibilityReport]) -> None:
        self._reports = reports

    def get(self, report_id: UUID) -> AccessibilityReport | None:
        with self._lock:
            report = self._load().get(report_id)
            return report.model_copy(deep=True) if report else None

    def get_by_execution(self, execution_id: UUID) -> AccessibilityReport | None:
        with self._lock:
            for report in self._load().values():
                if report.execution_id == execution_id:
                    return report.model_copy(deep=True)
        return None

    def save(self, report: AccessibilityReport) -> AccessibilityReport:
        with self._lock:
            reports = dict(self._load())
            if report.execution_id is not None:
                for other in list(reports.values()):
                    if other.execution_id == report.execution_id and other.id != report.id:
                        raise ValueError(f"Execution {report.execution_id} already has an accessibility report")
            reports[report.id] = report.model_copy(deep=True)
            self._store(reports)
        return report

    def list_reports(self, project_id, source=None, limit=20, offset=0):
        with self._lock:
            rows = [
                r.model_copy(deep=True) for r in self._load().values()
                if r.project_id == project_id and (source is None or r.source == AccessibilitySource(source))
            ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        return rows[offset:offset + limit], len(rows)

    def list_by_batch(self, project_id: UUID, batch_id: str) -> list[AccessibilityReport]:
        with self._lock:
            rows = [
                r.model_copy(deep=True) for r in self._load().values()
                if r.project_id == project_id and batch_id and r.batch_id == batch_id
            ]
        return sorted(rows, key=lambda r: r.created_at)

    def delete_for_project(self, project_id: UUID) -> int:
        with self._lock:
            reports = dict(self._load())
            doomed = [rid for rid, r in reports.items() if r.project_id == project_id]
            for rid in doomed:
                del reports[rid]
            if doomed:
                self._store(reports)
            return len(doomed)

    def list_active_scans(self, project_id: UUID | None = None) -> list[AccessibilityReport]:
        with self._lock:
            rows = [
                r.model_copy(deep=True) for r in self._load().values()
                if r.source == AccessibilitySource.SCAN and r.status in _ACTIVE_STATUSES
                and (project_id is None or r.project_id == project_id)
            ]
        return sorted(rows, key=lambda r: r.created_at)

    def save_new_scan(self, report: AccessibilityReport) -> AccessibilityReport:
        # The process lock makes the check and the insert one step (the JSON store's
        # stand in for the Postgres partial unique index).
        with self._lock:
            return super().save_new_scan(report)


class JsonAccessibilityReportRepository(InMemoryAccessibilityReportRepository):
    _file_lock = threading.RLock()

    def __init__(self, path: Path | None = None):
        super().__init__()
        self.path = Path(path or ACCESSIBILITY_STORE_PATH)
        self._lock = self._file_lock

    def _load(self) -> dict[UUID, AccessibilityReport]:
        if not self.path.exists():
            return {}
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        # A damaged file raises (JSONDecodeError is a ValueError). Reading it as
        # empty would let the next save replace every report with one.
        raw = json.loads(text)
        return {UUID(k): AccessibilityReport.model_validate(v) for k, v in raw.get("accessibility_reports", {}).items()}

    def _store(self, reports: dict[UUID, AccessibilityReport]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"accessibility_reports": {str(k): v.model_dump(mode="json") for k, v in reports.items()}}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".accessibility_reports_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


_repository: AccessibilityReportRepository | None = None
_repository_lock = threading.Lock()


def get_accessibility_repository() -> AccessibilityReportRepository:
    """The accessibility report store for the active repository provider (json or postgres)."""
    global _repository
    with _repository_lock:
        if _repository is None:
            from backend.config.settings import get_settings

            if get_settings().repository.provider == "postgres":
                from backend.repository.postgres_accessibility_repository import (
                    PostgresAccessibilityReportRepository,
                )

                _repository = PostgresAccessibilityReportRepository()
            else:
                _repository = JsonAccessibilityReportRepository()
        return _repository


def set_accessibility_repository(repository: AccessibilityReportRepository | None) -> None:
    """Swap the active store. Tests use this; None goes back to the provider default."""
    global _repository
    with _repository_lock:
        _repository = repository
