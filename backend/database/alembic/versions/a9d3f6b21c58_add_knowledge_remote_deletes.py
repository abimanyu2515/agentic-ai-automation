"""Add the pending RAGFlow delete table (spec 0007, AC-1 and AC-2).

One row per remote target still to delete. `project_id` has no foreign key,
so a row outlives its project. Uniqueness uses two partial unique indexes
because PostgreSQL treats nulls as distinct.

The test suite's `create_all` can make this table on the dev database before
Alembic runs, so the table and each index are created only when missing.

Revision ID: a9d3f6b21c58
Revises: d4e8a1b6c372
Create Date: 2026-11-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a9d3f6b21c58"
down_revision: Union[str, Sequence[str], None] = "d4e8a1b6c372"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "knowledge_remote_deletes"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("dataset_id", sa.String(100), nullable=False),
            sa.Column("remote_doc_id", sa.String(100), nullable=True),
            sa.Column("reason", sa.String(50), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        )
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(TABLE)}
    if "uq_knowledge_remote_deletes_document" not in existing:
        op.create_index(
            "uq_knowledge_remote_deletes_document", TABLE, ["dataset_id", "remote_doc_id"],
            unique=True, postgresql_where=sa.text("kind = 'document'"),
        )
    if "uq_knowledge_remote_deletes_dataset" not in existing:
        op.create_index(
            "uq_knowledge_remote_deletes_dataset", TABLE, ["dataset_id"],
            unique=True, postgresql_where=sa.text("kind = 'dataset'"),
        )
    if "ix_knowledge_remote_deletes_project_id" not in existing:
        op.create_index("ix_knowledge_remote_deletes_project_id", TABLE, ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_remote_deletes_project_id", table_name=TABLE)
    op.drop_index("uq_knowledge_remote_deletes_dataset", table_name=TABLE)
    op.drop_index("uq_knowledge_remote_deletes_document", table_name=TABLE)
    op.drop_table(TABLE)
