"""Add project safety and script validation state.

Revision ID: a12c4e7b93d1
Revises: 5803560644cc
Create Date: 2026-10-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a12c4e7b93d1"
down_revision: Union[str, Sequence[str], None] = "5803560644cc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "is_safe_test_environment",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "test_cases",
        sa.Column(
            "script_validation_status",
            sa.String(length=30),
            server_default=sa.text("'unverified'"),
            nullable=False,
        ),
    )
    op.add_column(
        "test_cases",
        sa.Column(
            "script_validation_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "test_cases",
        sa.Column("script_validation_completed_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "test_cases",
        sa.Column("script_validation_diagnostic", sa.Text()),
    )


def downgrade() -> None:
    op.drop_column("test_cases", "script_validation_diagnostic")
    op.drop_column("test_cases", "script_validation_completed_at")
    op.drop_column("test_cases", "script_validation_attempts")
    op.drop_column("test_cases", "script_validation_status")
    op.drop_column("projects", "is_safe_test_environment")
