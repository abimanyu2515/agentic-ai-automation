"""Merge the self healing and accessibility migration branches.

Both branches start at c3f8a1d7e925 and touch different tables, so this
revision only joins the two heads.

Revision ID: d9b4f2a6c871
Revises: f4c2d8e6a915, c2a8f5e17b49
Create Date: 2026-11-27
"""
from typing import Sequence, Union


revision: str = "d9b4f2a6c871"
down_revision: Union[str, Sequence[str], None] = ("f4c2d8e6a915", "c2a8f5e17b49")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
