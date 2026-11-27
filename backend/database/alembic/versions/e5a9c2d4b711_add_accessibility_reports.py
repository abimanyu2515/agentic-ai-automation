"""Add accessibility reports.

Creates the `accessibility_reports` table written by the Accessibility Agent
(spec 0011): one report per test run (unique `execution_id`) or per standalone
scan, removed with its project.

Revision ID: e5a9c2d4b711
Revises: c3f8a1d7e925
Create Date: 2026-11-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e5a9c2d4b711"
down_revision: Union[str, Sequence[str], None] = "c3f8a1d7e925"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accessibility_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(20), nullable=False, server_default="execution"),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True, unique=True),
        sa.Column("batch_id", sa.String(100), nullable=True),
        sa.Column("gate_mode", sa.String(10), nullable=False, server_default="warn"),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="succeeded"),
        sa.Column("error", sa.String(50), nullable=True),
        sa.Column("login_status", sa.String(20), nullable=False, server_default="not_requested"),
        sa.Column("wcag_tags", sa.JSON(), nullable=False),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("gate_status", sa.String(20), nullable=False, server_default="NOT_SCANNED"),
        sa.Column("gate_reason", sa.String(50), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_accessibility_reports_batch_id", "accessibility_reports", ["batch_id"])
    op.create_index("ix_accessibility_reports_project_created", "accessibility_reports", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_accessibility_reports_project_created", table_name="accessibility_reports")
    op.drop_index("ix_accessibility_reports_batch_id", table_name="accessibility_reports")
    op.drop_table("accessibility_reports")
