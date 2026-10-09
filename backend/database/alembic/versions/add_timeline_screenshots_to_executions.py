"""Add timeline and screenshots columns to executions table

Revision ID: add_timeline_screenshots
Revises: 14cf5447c245
Create Date: 2026-07-19 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'add_timeline_screenshots'
down_revision: Union[str, Sequence[str], None] = '14cf5447c245'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add timeline column as JSON
    op.add_column('executions', 
        sa.Column('timeline', sa.JSON(), nullable=True)
    )
    
    # Add screenshots column as JSON
    op.add_column('executions',
        sa.Column('screenshots', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Remove screenshots column
    op.drop_column('executions', 'screenshots')
    
    # Remove timeline column
    op.drop_column('executions', 'timeline')
