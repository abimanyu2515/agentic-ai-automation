"""Add groundedness fields to test cases (spec 0006, AC-26).

Revision ID: c6f0a2d84e19
Revises: b8c4e2f17d93
Create Date: 2026-11-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6f0a2d84e19"
down_revision: Union[str, Sequence[str], None] = "b8c4e2f17d93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_cases", sa.Column("groundedness_score", sa.Float(), nullable=True))
    op.add_column(
        "test_cases",
        sa.Column("unsupported_claims", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("test_cases", "unsupported_claims")
    op.drop_column("test_cases", "groundedness_score")
