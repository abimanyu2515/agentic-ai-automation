"""PostgreSQL bug report store.

Each call uses its own short lived session. Any failure rolls the session back
and closes it before the error is raised, so a failed transaction is never
reused. The full report is kept in `payload`; the other columns are lookup keys.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select

from backend.database.db import SessionLocal
from backend.database.db_models import BugReportDB
from backend.models.bug_report import OPEN_STATUSES, BugReport, BugReportStatus
from backend.repository.bug_report_repository import BugReportRepository


def _report(row: BugReportDB) -> BugReport:
    return BugReport.model_validate(row.payload)


class PostgresBugReportRepository(BugReportRepository):
    @contextmanager
    def _session(self):
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_reports(self, project_id: UUID, status: BugReportStatus | None = None) -> list[BugReport]:
        with self._session() as session:
            stmt = select(BugReportDB).where(BugReportDB.project_id == project_id, BugReportDB.deleted_at.is_(None))
            if status is not None:
                stmt = stmt.where(BugReportDB.status == BugReportStatus(status).value)
            stmt = stmt.order_by(BugReportDB.created_at.desc())
            return [_report(row) for row in session.scalars(stmt).all()]

    def get(self, report_id: UUID) -> BugReport | None:
        with self._session() as session:
            row = session.get(BugReportDB, report_id)
            return _report(row) if row and row.deleted_at is None else None

    def save(self, report: BugReport) -> BugReport:
        payload = report.model_dump(mode="json")
        columns = {
            "project_id": report.project_id,
            "execution_id": report.execution_id,
            "test_case_id": report.test_case_id,
            "status": report.status.value,
            "error_signature": report.error_signature,
            "jira_key": report.jira_key,
            "duplicate_of_jira_key": report.duplicate_of_jira_key,
            "payload": payload,
            "created_at": report.created_at,
            "updated_at": report.updated_at,
            "deleted_at": report.deleted_at,
        }
        with self._session() as session:
            row = session.get(BugReportDB, report.id)
            if row is None:
                session.add(BugReportDB(id=report.id, **columns))
            else:
                for name, value in columns.items():
                    setattr(row, name, value)
        return report

    def soft_delete(self, report_id: UUID) -> bool:
        with self._session() as session:
            row = session.get(BugReportDB, report_id)
            if not row or row.deleted_at is not None:
                return False
            now = datetime.now(timezone.utc)
            row.deleted_at = now
            row.payload = {**row.payload, "deleted_at": now.isoformat()}
            return True

    def find_open_by_signature(self, project_id: UUID, test_case_id: UUID, signature: str) -> BugReport | None:
        if not signature:
            return None
        with self._session() as session:
            stmt = (
                select(BugReportDB)
                .where(
                    BugReportDB.project_id == project_id,
                    BugReportDB.test_case_id == test_case_id,
                    BugReportDB.error_signature == signature,
                    BugReportDB.status.in_([s.value for s in OPEN_STATUSES]),
                    BugReportDB.deleted_at.is_(None),
                )
                .order_by(BugReportDB.created_at.desc())
                .limit(1)
            )
            row = session.scalar(stmt)
            return _report(row) if row else None

    def list_by_jira_key(self, jira_key: str) -> list[BugReport]:
        with self._session() as session:
            stmt = select(BugReportDB).where(
                or_(BugReportDB.jira_key == jira_key, BugReportDB.duplicate_of_jira_key == jira_key),
                BugReportDB.deleted_at.is_(None),
            )
            return [_report(row) for row in session.scalars(stmt).all()]
