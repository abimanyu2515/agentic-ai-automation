"""Persist generated test data on test cases.

Revision ID: 8d3c9a71b642
Revises: fef1658bde83
Create Date: 2026-10-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d3c9a71b642"
down_revision: Union[str, Sequence[str], None] = "fef1658bde83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_cases",
        sa.Column("test_data", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "test_cases",
        sa.Column("test_data_sets", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.alter_column("test_cases", "test_data", server_default=None)
    op.alter_column("test_cases", "test_data_sets", server_default=None)


def downgrade() -> None:
    op.drop_column("test_cases", "test_data_sets")
    op.drop_column("test_cases", "test_data")
