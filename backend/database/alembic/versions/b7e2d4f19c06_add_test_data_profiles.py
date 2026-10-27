"""Add per dataset test data profiles to test cases.

Revision ID: b7e2d4f19c06
Revises: a12c4e7b93d1
Create Date: 2026-10-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e2d4f19c06"
down_revision: Union[str, Sequence[str], None] = "a12c4e7b93d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "test_cases",
        sa.Column(
            "test_data_profiles",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("test_cases", "test_data_profiles")
