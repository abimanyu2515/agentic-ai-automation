"""merge project configuration and execution migrations

Revision ID: fef1658bde83
Revises: 4e73efadb26f, add_timeline_screenshots
Create Date: 2026-10-13 09:56:15.103962

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fef1658bde83'
down_revision: Union[str, Sequence[str], None] = ('4e73efadb26f', 'add_timeline_screenshots')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
