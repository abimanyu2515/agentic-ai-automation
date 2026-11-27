"""Add project accessibility settings.

Adds the Accessibility Agent's project settings (spec 0011, AC-13):
`a11y_enabled` (default true), `a11y_gate_mode` (default `warn`), and
`a11y_block_severity` (default `serious`). Existing projects get the defaults.

Revision ID: a7d3e9b1c540
Revises: e5a9c2d4b711
Create Date: 2026-11-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7d3e9b1c540"
down_revision: Union[str, Sequence[str], None] = "e5a9c2d4b711"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("a11y_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "projects",
        sa.Column("a11y_gate_mode", sa.String(10), nullable=False, server_default="warn"),
    )
    op.add_column(
        "projects",
        sa.Column("a11y_block_severity", sa.String(10), nullable=False, server_default="serious"),
    )


def downgrade() -> None:
    op.drop_column("projects", "a11y_block_severity")
    op.drop_column("projects", "a11y_gate_mode")
    op.drop_column("projects", "a11y_enabled")
