"""Add script heals for RCA driven script repair (spec 0011 amendment).

Creates `test_case_script_heals`.

Revision ID: f4c2d8e6a915
Revises: e1a9c4b7d203
Create Date: 2026-11-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f4c2d8e6a915"
down_revision: Union[str, Sequence[str], None] = "e1a9c4b7d203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "test_case_script_heals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "test_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("old_script", sa.Text(), nullable=False),
        sa.Column("new_script", sa.Text(), nullable=False),
        sa.Column("rca_category", sa.String(100), nullable=False),
        sa.Column("root_cause_summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("failing_step", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_test_case_script_heals_test_case_created", "test_case_script_heals", ["test_case_id", "created_at"]
    )
    op.create_index("ix_test_case_script_heals_project_status", "test_case_script_heals", ["project_id", "status"])
    op.create_index(
        "uq_test_case_script_heals_one_suggested",
        "test_case_script_heals",
        ["test_case_id"],
        unique=True,
        postgresql_where=sa.text("status = 'suggested'"),
        sqlite_where=sa.text("status = 'suggested'"),
    )


def downgrade() -> None:
    op.drop_index("uq_test_case_script_heals_one_suggested", table_name="test_case_script_heals")
    op.drop_index("ix_test_case_script_heals_project_status", table_name="test_case_script_heals")
    op.drop_index("ix_test_case_script_heals_test_case_created", table_name="test_case_script_heals")
    op.drop_table("test_case_script_heals")
