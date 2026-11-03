"""Add the object repository tables (spec 0004).

Revision ID: c3f8a1d2e905
Revises: b7e2d4f19c06
Create Date: 2026-11-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c3f8a1d2e905"
down_revision: Union[str, Sequence[str], None] = "b7e2d4f19c06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _soft_delete_columns() -> list[sa.Column]:
    return [
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "deleted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "object_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url_pattern", sa.String(500), nullable=False),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_soft_delete_columns(),
    )
    op.create_index("ix_object_pages_project_id", "object_pages", ["project_id"])
    op.create_index(
        "uq_object_pages_project_name",
        "object_pages",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_object_pages_project_pattern",
        "object_pages",
        ["project_id", "url_pattern"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )

    op.create_table(
        "object_elements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("object_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("element_type", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("health", sa.String(20), nullable=False, server_default=sa.text("'healthy'")),
        sa.Column("manually_edited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_locator_index", sa.Integer(), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("consecutive_failed_runs", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("applied_execution_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_soft_delete_columns(),
    )
    op.create_index("ix_object_elements_page_id", "object_elements", ["page_id"])
    op.create_index(
        "uq_object_elements_page_name",
        "object_elements",
        ["page_id", "name"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_object_elements_page_fingerprint",
        "object_elements",
        ["page_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("fingerprint IS NOT NULL"),
    )

    op.create_table(
        "object_locators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "element_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("object_elements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(20), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("element_id", "position", name="uq_object_locators_element_position"),
    )
    op.create_index("ix_object_locators_element_id", "object_locators", ["element_id"])


def downgrade() -> None:
    op.drop_index("ix_object_locators_element_id", table_name="object_locators")
    op.drop_table("object_locators")
    op.drop_index("uq_object_elements_page_fingerprint", table_name="object_elements")
    op.drop_index("uq_object_elements_page_name", table_name="object_elements")
    op.drop_index("ix_object_elements_page_id", table_name="object_elements")
    op.drop_table("object_elements")
    op.drop_index("uq_object_pages_project_pattern", table_name="object_pages")
    op.drop_index("uq_object_pages_project_name", table_name="object_pages")
    op.drop_index("ix_object_pages_project_id", table_name="object_pages")
    op.drop_table("object_pages")
