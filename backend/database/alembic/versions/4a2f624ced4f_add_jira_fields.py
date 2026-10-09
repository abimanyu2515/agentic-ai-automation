"""add_jira_fields

Revision ID: 4a2f624ced4f
Revises: 1a821cd835fd
Create Date: 2026-07-14 20:54:19.768001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a2f624ced4f'
down_revision: Union[str, Sequence[str], None] = '1a821cd835fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('jira_project_key', sa.String(length=100), nullable=True))
    op.add_column('requirements', sa.Column('jira_issue_key', sa.String(length=100), nullable=True))
    op.add_column('requirements', sa.Column('jira_issue_url', sa.String(length=500), nullable=True))
    op.add_column('requirements', sa.Column('jira_sync_status', sa.String(length=50), nullable=True))
    op.add_column('requirements', sa.Column('jira_last_synced_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_requirements_jira_issue_key'), 'requirements', ['jira_issue_key'], unique=False)
    
    op.add_column('scenarios', sa.Column('jira_issue_key', sa.String(length=100), nullable=True))
    op.add_column('scenarios', sa.Column('jira_issue_url', sa.String(length=500), nullable=True))
    op.add_column('scenarios', sa.Column('jira_sync_status', sa.String(length=50), nullable=True))
    op.add_column('scenarios', sa.Column('jira_last_synced_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_scenarios_jira_issue_key'), 'scenarios', ['jira_issue_key'], unique=False)
    
    op.add_column('test_cases', sa.Column('jira_issue_key', sa.String(length=100), nullable=True))
    op.add_column('test_cases', sa.Column('jira_issue_url', sa.String(length=500), nullable=True))
    op.add_column('test_cases', sa.Column('jira_sync_status', sa.String(length=50), nullable=True))
    op.add_column('test_cases', sa.Column('jira_last_synced_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f('ix_test_cases_jira_issue_key'), 'test_cases', ['jira_issue_key'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_test_cases_jira_issue_key'), table_name='test_cases')
    op.drop_column('test_cases', 'jira_last_synced_at')
    op.drop_column('test_cases', 'jira_sync_status')
    op.drop_column('test_cases', 'jira_issue_url')
    op.drop_column('test_cases', 'jira_issue_key')
    
    op.drop_index(op.f('ix_scenarios_jira_issue_key'), table_name='scenarios')
    op.drop_column('scenarios', 'jira_last_synced_at')
    op.drop_column('scenarios', 'jira_sync_status')
    op.drop_column('scenarios', 'jira_issue_url')
    op.drop_column('scenarios', 'jira_issue_key')
    
    op.drop_index(op.f('ix_requirements_jira_issue_key'), table_name='requirements')
    op.drop_column('requirements', 'jira_last_synced_at')
    op.drop_column('requirements', 'jira_sync_status')
    op.drop_column('requirements', 'jira_issue_url')
    op.drop_column('requirements', 'jira_issue_key')
    
    op.drop_column('projects', 'jira_project_key')
