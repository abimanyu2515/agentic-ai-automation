"""Add bug reports.

Creates the `bug_reports` table written by the Bug Reporting Agent, links each
execution to its report (`executions.bug_report_id`), and adds the per project
auto-submit settings.

Revision ID: c3f8a1d7e925
Revises: b7e2c4f91a30
Create Date: 2026-11-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c3f8a1d7e925"
down_revision: Union[str, Sequence[str], None] = "b7e2c4f91a30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bug_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("error_signature", sa.String(64), nullable=False, server_default=""),
        sa.Column("jira_key", sa.String(100), nullable=True),
        sa.Column("duplicate_of_jira_key", sa.String(100), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_bug_reports_project_created", "bug_reports", ["project_id", sa.text("created_at DESC")])
    op.create_index("ix_bug_reports_signature", "bug_reports", ["project_id", "test_case_id", "error_signature"])
    op.create_index("ix_bug_reports_execution_id", "bug_reports", ["execution_id"])
    op.create_index("ix_bug_reports_jira_key", "bug_reports", ["jira_key"])

    op.add_column("executions", sa.Column("bug_report_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "projects",
        sa.Column("bug_auto_submit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "projects",
        sa.Column("bug_auto_submit_min_confidence", sa.Float(), nullable=False, server_default=sa.text("0.85")),
    )


def downgrade() -> None:
    op.drop_column("projects", "bug_auto_submit_min_confidence")
    op.drop_column("projects", "bug_auto_submit")
    op.drop_column("executions", "bug_report_id")
    op.drop_index("ix_bug_reports_jira_key", table_name="bug_reports")
    op.drop_index("ix_bug_reports_execution_id", table_name="bug_reports")
    op.drop_index("ix_bug_reports_signature", table_name="bug_reports")
    op.drop_index("ix_bug_reports_project_created", table_name="bug_reports")
    op.drop_table("bug_reports")
