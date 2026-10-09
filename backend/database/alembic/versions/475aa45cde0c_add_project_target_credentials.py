"""add_project_target_credentials

Revision ID: 475aa45cde0c
Revises: 1c9c12f76c45
Create Date: 2026-07-15 12:15:10.232115

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '475aa45cde0c'
down_revision: Union[str, Sequence[str], None] = '1c9c12f76c45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('target_url', sa.String(length=500), nullable=True))
    op.add_column('projects', sa.Column('target_username', sa.String(length=255), nullable=True))
    op.add_column('projects', sa.Column('target_password_enc', sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'target_password_enc')
    op.drop_column('projects', 'target_username')
    op.drop_column('projects', 'target_url')
