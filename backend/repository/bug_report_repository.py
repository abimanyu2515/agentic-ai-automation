"""
Bug report persistence.

`BugReportRepository` is the interface. `InMemoryBugReportRepository` keeps
reports in a dict (tests). `JsonBugReportRepository` stores them in
`backend/database/bug_report_store.json` under one process lock and writes
through a temp file plus an atomic rename. `PostgresBugReportRepository`
(postgres_bug_report_repository.py) is the SQL implementation. The active
store follows the same provider setting as `get_project_repository()`.
"""

import json
import os
import tempfile
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from backend.models.bug_report import OPEN_STATUSES, BugReport, BugReportStatus

BUG_REPORT_STORE_PATH = Path(__file__).parent.parent / "database" / "bug_report_store.json"


class BugReportRepository(ABC):
    @abstractmethod
    def list_reports(self, project_id: UUID, status: BugReportStatus | None = None) -> list[BugReport]:
        """Newest first, excluding deleted reports."""

    @abstractmethod
    def get(self, report_id: UUID) -> BugReport | None:
        pass

    @abstractmethod
    def save(self, report: BugReport) -> BugReport:
        """Insert or replace by id."""

    @abstractmethod
    def soft_delete(self, report_id: UUID) -> bool:
        pass

    # Lookups built on list(); implementations may override with indexed queries.

    def find_open_by_signature(self, project_id: UUID, test_case_id: UUID, signature: str) -> BugReport | None:
        if not signature:
            return None
        for report in self.list_reports(project_id):
            if (
                report.test_case_id == test_case_id
                and report.error_signature == signature
                and report.status in OPEN_STATUSES
            ):
                return report
        return None

    def get_by_execution(self, project_id: UUID, execution_id: UUID) -> BugReport | None:
        for report in self.list_reports(project_id):
            if report.execution_id == execution_id or any(o.execution_id == execution_id for o in report.occurrences):
                return report
        return None

    @abstractmethod
    def list_by_jira_key(self, jira_key: str) -> list[BugReport]:
        pass


class InMemoryBugReportRepository(BugReportRepository):
    def __init__(self):
        self._reports: dict[UUID, BugReport] = {}
        self._lock = threading.RLock()

    def _all(self) -> list[BugReport]:
        return list(self._reports.values())

    def list_reports(self, project_id: UUID, status: BugReportStatus | None = None) -> list[BugReport]:
        with self._lock:
            rows = [
                r.model_copy(deep=True) for r in self._all()
                if r.project_id == project_id and r.deleted_at is None and (status is None or r.status == status)
            ]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    def get(self, report_id: UUID) -> BugReport | None:
        with self._lock:
            report = self._reports.get(report_id)
            return report.model_copy(deep=True) if report and report.deleted_at is None else None

    def save(self, report: BugReport) -> BugReport:
        with self._lock:
            self._reports[report.id] = report.model_copy(deep=True)
        return report

    def soft_delete(self, report_id: UUID) -> bool:
        with self._lock:
            report = self._reports.get(report_id)
            if not report or report.deleted_at is not None:
                return False
            report.deleted_at = datetime.now(timezone.utc)
            return True

    def list_by_jira_key(self, jira_key: str) -> list[BugReport]:
        with self._lock:
            return [
                r.model_copy(deep=True) for r in self._all()
                if r.deleted_at is None and jira_key in (r.jira_key, r.duplicate_of_jira_key)
            ]


class JsonBugReportRepository(InMemoryBugReportRepository):
    _file_lock = threading.RLock()

    def __init__(self, path: Path | None = None):
        super().__init__()
        self.path = Path(path or BUG_REPORT_STORE_PATH)
        self._lock = self._file_lock

    def _read(self) -> dict[UUID, BugReport]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            return {}
        return {UUID(k): BugReport.model_validate(v) for k, v in raw.get("bug_reports", {}).items()}

    def _write(self, reports: dict[UUID, BugReport]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"bug_reports": {str(k): v.model_dump(mode="json") for k, v in reports.items()}}
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".bug_reports_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def _all(self) -> list[BugReport]:
        return list(self._read().values())

    def get(self, report_id: UUID) -> BugReport | None:
        with self._lock:
            report = self._read().get(report_id)
            return report if report and report.deleted_at is None else None

    def save(self, report: BugReport) -> BugReport:
        with self._lock:
            reports = self._read()
            reports[report.id] = report
            self._write(reports)
        return report

    def soft_delete(self, report_id: UUID) -> bool:
        with self._lock:
            reports = self._read()
            report = reports.get(report_id)
            if not report or report.deleted_at is not None:
                return False
            report.deleted_at = datetime.now(timezone.utc)
            self._write(reports)
            return True


_bug_report_repository: BugReportRepository | None = None
_bug_report_repository_lock = threading.Lock()


def get_bug_report_repository() -> BugReportRepository:
    """The bug report store for the active repository provider (json or postgres)."""
    global _bug_report_repository
    with _bug_report_repository_lock:
        if _bug_report_repository is None:
            from backend.config.settings import get_settings

            if get_settings().repository.provider == "postgres":
                from backend.repository.postgres_bug_report_repository import PostgresBugReportRepository

                _bug_report_repository = PostgresBugReportRepository()
            else:
                _bug_report_repository = JsonBugReportRepository()
        return _bug_report_repository


def set_bug_report_repository(repository: BugReportRepository | None) -> None:
    """Swap the active store. Tests use this; None goes back to the provider default."""
    global _bug_report_repository
    with _bug_report_repository_lock:
        _bug_report_repository = repository
