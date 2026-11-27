"""Give batch runs their own batch id.

Adds the nullable `executions.batch_id` column and the `(project_id, batch_id)`
index (spec 0012, AC-10). Batch runs saved before this change have no batch,
because PostgreSQL never kept their id in `test_cycle_id`.

Revision ID: c2a8f5e17b49
Revises: b8e2d6f4a913
Create Date: 2026-11-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c2a8f5e17b49"
down_revision: Union[str, Sequence[str], None] = "b8e2d6f4a913"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_executions_project_batch", "executions", ["project_id", "batch_id"])


def downgrade() -> None:
    op.drop_index("ix_executions_project_batch", table_name="executions")
    op.drop_column("executions", "batch_id")
