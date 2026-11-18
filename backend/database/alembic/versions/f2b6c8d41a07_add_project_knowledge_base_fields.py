"""Per project knowledge base fields (spec 0006, slice 1).

Adds projects.rag_dataset_id, documents.remote_doc_id, and documents.error.
All nullable, so earlier code keeps working against the new schema.

Revision ID: f2b6c8d41a07
Revises: e7a3c5d91b48
Create Date: 2026-11-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2b6c8d41a07"
down_revision: Union[str, Sequence[str], None] = "e7a3c5d91b48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("rag_dataset_id", sa.String(100), nullable=True))
    op.add_column("documents", sa.Column("remote_doc_id", sa.String(100), nullable=True))
    op.add_column("documents", sa.Column("error", sa.Text(), nullable=True))
    op.create_index("ix_documents_project_remote_doc", "documents", ["project_id", "remote_doc_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_project_remote_doc", table_name="documents")
    op.drop_column("documents", "error")
    op.drop_column("documents", "remote_doc_id")
    op.drop_column("projects", "rag_dataset_id")
