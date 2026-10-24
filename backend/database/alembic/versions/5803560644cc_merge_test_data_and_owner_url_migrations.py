"""merge test data and owner URL migrations

Revision ID: 5803560644cc
Revises: 8d3c9a71b642, 9c1f8f0e2a11
Create Date: 2026-10-23 13:52:23.040638

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5803560644cc'
down_revision: Union[str, Sequence[str], None] = ('8d3c9a71b642', '9c1f8f0e2a11')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
