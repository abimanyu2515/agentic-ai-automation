"""Add self healing locators (spec 0011).

Creates `object_locator_heals`, adds `object_elements.locators_version`,
`projects.self_healing_enabled`, and the heal rerun link on `executions`.

Revision ID: e1a9c4b7d203
Revises: c3f8a1d7e925
Create Date: 2026-11-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e1a9c4b7d203"
down_revision: Union[str, Sequence[str], None] = "c3f8a1d7e925"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "object_locator_heals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "element_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("object_elements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("old_locators", sa.JSON(), nullable=False),
        sa.Column("new_locators", sa.JSON(), nullable=False),
        sa.Column("old_fingerprint", sa.String(64), nullable=True),
        sa.Column("candidate_fingerprint", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_source", sa.String(20), nullable=False),
        sa.Column("locators_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_object_locator_heals_element_created", "object_locator_heals", ["element_id", "created_at"])
    op.create_index("ix_object_locator_heals_project_status", "object_locator_heals", ["project_id", "status"])
    op.create_index(
        "uq_object_locator_heals_one_suggested",
        "object_locator_heals",
        ["element_id"],
        unique=True,
        postgresql_where=sa.text("status = 'suggested'"),
        sqlite_where=sa.text("status = 'suggested'"),
    )

    op.add_column(
        "object_elements",
        sa.Column("locators_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "projects",
        sa.Column("self_healing_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column("executions", sa.Column("heal_rerun_of", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("executions", sa.Column("heal_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("executions", "heal_ids")
    op.drop_column("executions", "heal_rerun_of")
    op.drop_column("projects", "self_healing_enabled")
    op.drop_column("object_elements", "locators_version")
    op.drop_index("uq_object_locator_heals_one_suggested", table_name="object_locator_heals")
    op.drop_index("ix_object_locator_heals_project_status", table_name="object_locator_heals")
    op.drop_index("ix_object_locator_heals_element_created", table_name="object_locator_heals")
    op.drop_table("object_locator_heals")
