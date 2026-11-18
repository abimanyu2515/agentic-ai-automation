"""Add the project configuration snapshot to executions (spec 0001, AC-4 and AC-8).

Nullable, so runs saved before this revision stay valid (migration plan phase 1,
compatibility mode). The snapshot never holds a password.

Revision ID: d4e8a1b6c372
Revises: c6f0a2d84e19
Create Date: 2026-11-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e8a1b6c372"
down_revision: Union[str, Sequence[str], None] = "c6f0a2d84e19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("project_config_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("executions", "project_config_snapshot")
