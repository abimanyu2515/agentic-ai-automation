"""PostgreSQL accessibility report store (spec 0011, AC-15).

Each call uses its own short lived session. Any failure rolls the session back
and closes it before the error is raised, so a failed transaction is never reused.
"""

from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.database.db import SessionLocal
from backend.database.db_models import AccessibilityReportDB
from backend.models.accessibility import AccessibilityReport, AccessibilityReportStatus, AccessibilitySource
from backend.repository.accessibility_repository import ActiveScanExists, AccessibilityReportRepository

_ACTIVE = (AccessibilityReportStatus.QUEUED.value, AccessibilityReportStatus.RUNNING.value)

_COLUMNS = (
    "project_id", "source", "execution_id", "batch_id", "gate_mode", "test_case_id", "status",
    "error", "login_status", "wcag_tags", "pages", "truncated", "findings", "counts",
    "gate_status", "gate_reason", "created_by", "created_at", "finished_at",
)
# Stored as JSON, so they keep their JSON form; the rest keep Python types.
_JSON_COLUMNS = {"wcag_tags", "pages", "findings", "counts"}


def _report(row: AccessibilityReportDB) -> AccessibilityReport:
    return AccessibilityReport.model_validate({"id": row.id, **{name: getattr(row, name) for name in _COLUMNS}})


def _columns(report: AccessibilityReport) -> dict:
    as_json = report.model_dump(mode="json", include=_JSON_COLUMNS)
    plain = report.model_dump(exclude={"id", *_JSON_COLUMNS})
    values = {**plain, **as_json}
    for name in ("source", "gate_mode", "status", "gate_status"):
        values[name] = getattr(values[name], "value", values[name])
    return {name: values[name] for name in _COLUMNS}


class PostgresAccessibilityReportRepository(AccessibilityReportRepository):
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

    def get(self, report_id: UUID) -> AccessibilityReport | None:
        with self._session() as session:
            row = session.get(AccessibilityReportDB, report_id)
            return _report(row) if row else None

    def get_by_execution(self, execution_id: UUID) -> AccessibilityReport | None:
        with self._session() as session:
            row = session.scalar(select(AccessibilityReportDB).where(AccessibilityReportDB.execution_id == execution_id))
            return _report(row) if row else None

    def save(self, report: AccessibilityReport) -> AccessibilityReport:
        columns = _columns(report)
        with self._session() as session:
            row = session.get(AccessibilityReportDB, report.id)
            if row is None:
                session.add(AccessibilityReportDB(id=report.id, **columns))
            else:
                for name, value in columns.items():
                    setattr(row, name, value)
        return report

    def list_reports(self, project_id, source=None, limit=20, offset=0):
        with self._session() as session:
            where = [AccessibilityReportDB.project_id == project_id]
            if source is not None:
                where.append(AccessibilityReportDB.source == AccessibilitySource(source).value)
            total = session.scalar(select(func.count()).select_from(AccessibilityReportDB).where(*where)) or 0
            stmt = (
                select(AccessibilityReportDB)
                .where(*where)
                .order_by(AccessibilityReportDB.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return [_report(row) for row in session.scalars(stmt).all()], int(total)

    def list_by_batch(self, project_id: UUID, batch_id: str) -> list[AccessibilityReport]:
        if not batch_id:
            return []
        with self._session() as session:
            stmt = (
                select(AccessibilityReportDB)
                .where(AccessibilityReportDB.project_id == project_id, AccessibilityReportDB.batch_id == batch_id)
                .order_by(AccessibilityReportDB.created_at)
            )
            return [_report(row) for row in session.scalars(stmt).all()]

    def delete_for_project(self, project_id: UUID) -> int:
        with self._session() as session:
            result = session.execute(delete(AccessibilityReportDB).where(AccessibilityReportDB.project_id == project_id))
            return int(result.rowcount or 0)

    def list_active_scans(self, project_id: UUID | None = None) -> list[AccessibilityReport]:
        with self._session() as session:
            where = [
                AccessibilityReportDB.source == AccessibilitySource.SCAN.value,
                AccessibilityReportDB.status.in_(_ACTIVE),
            ]
            if project_id is not None:
                where.append(AccessibilityReportDB.project_id == project_id)
            stmt = select(AccessibilityReportDB).where(*where).order_by(AccessibilityReportDB.created_at)
            return [_report(row) for row in session.scalars(stmt).all()]

    def save_new_scan(self, report: AccessibilityReport) -> AccessibilityReport:
        """The partial unique index `uq_accessibility_reports_active_scan` refuses a
        second queued or running scan, even across worker processes (AC-10)."""
        try:
            with self._session() as session:
                session.add(AccessibilityReportDB(id=report.id, **_columns(report)))
        except IntegrityError as exc:
            raise ActiveScanExists(str(report.project_id)) from exc
        return report
