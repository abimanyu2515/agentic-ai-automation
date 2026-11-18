"""Knowledge entries for indexed project sources (spec 0006, slice 3).

One row per (project_id, source_type, source_ref): approved test cases,
Jira stories, failed executions, and object repository pages. A new table
only, so earlier code keeps working against the new schema.

Revision ID: b8c4e2f17d93
Revises: a3d7f1c9e526
Create Date: 2026-11-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b8c4e2f17d93"
down_revision: Union[str, Sequence[str], None] = "a3d7f1c9e526"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_ref", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("remote_doc_id", sa.String(100), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="indexed"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "source_type", "source_ref", name="uq_knowledge_entries_source"),
    )
    op.create_index("ix_knowledge_entries_project_remote_doc", "knowledge_entries", ["project_id", "remote_doc_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_entries_project_remote_doc", table_name="knowledge_entries")
    op.drop_table("knowledge_entries")
