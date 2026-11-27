"""Allow one active standalone accessibility scan per project.

A partial unique index on `accessibility_reports(project_id)` covers only
standalone scans that are still queued or running (spec 0011, AC-10), so a
second scan for the same project is refused even across worker processes.

Revision ID: b8e2d6f4a913
Revises: f1c7b3e8d204
Create Date: 2026-11-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e2d6f4a913"
down_revision: Union[str, Sequence[str], None] = "f1c7b3e8d204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WHERE = "source = 'scan' AND status IN ('queued', 'running')"


def upgrade() -> None:
    # A scan left active by an older build would block the index; close it first.
    op.execute(
        "UPDATE accessibility_reports SET status = 'failed', error = 'timeout', "
        "gate_status = 'NOT_SCANNED', gate_reason = 'scan_error', finished_at = now() "
        f"WHERE {_WHERE}"
    )
    op.create_index(
        "uq_accessibility_reports_active_scan",
        "accessibility_reports",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(_WHERE),
    )


def downgrade() -> None:
    op.drop_index("uq_accessibility_reports_active_scan", table_name="accessibility_reports")
