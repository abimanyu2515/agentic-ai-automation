"""merge chat and token usage migrations

Revision ID: e7a3c5d91b48
Revises: d5e1a7c3b210, d4e9b2c7a1f3
Create Date: 2026-11-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7a3c5d91b48'
down_revision: Union[str, Sequence[str], None] = ('d5e1a7c3b210', 'd4e9b2c7a1f3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
