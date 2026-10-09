"""add_releases_cycles_and_triaging

Revision ID: 1c9c12f76c45
Revises: 4a2f624ced4f
Create Date: 2026-07-15 11:37:43.930207

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c9c12f76c45'
down_revision: Union[str, Sequence[str], None] = '4a2f624ced4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create releases table
    op.create_table('releases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=False, server_default='Active'),
    sa.Column('start_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('end_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_releases_project_id'), 'releases', ['project_id'], unique=False)
    
    # 2. Create test_cycles table
    op.create_table('test_cycles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('release_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=False, server_default='Active'),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.ForeignKeyConstraint(['release_id'], ['releases.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_test_cycles_release_id'), 'test_cycles', ['release_id'], unique=False)

    # 3. Add columns to executions
    op.add_column('executions', sa.Column('test_cycle_id', sa.UUID(), nullable=True))
    op.add_column('executions', sa.Column('failure_category', sa.String(length=100), nullable=True))
    op.add_column('executions', sa.Column('root_cause_summary', sa.Text(), nullable=True))
    op.add_column('executions', sa.Column('suggest_retry', sa.Boolean(), nullable=True, server_default='0'))
    op.add_column('executions', sa.Column('retest_pending_candidate', sa.Boolean(), nullable=True, server_default='0'))
    op.add_column('executions', sa.Column('jira_bug_id', sa.String(length=100), nullable=True))
    op.add_column('executions', sa.Column('jira_bug_url', sa.String(length=500), nullable=True))
    
    op.create_index(op.f('ix_executions_test_cycle_id'), 'executions', ['test_cycle_id'], unique=False)
    op.create_foreign_key('fk_executions_test_cycle_id', 'executions', 'test_cycles', ['test_cycle_id'], ['id'], ondelete='SET NULL')

    # 4. Add columns to requirements
    op.add_column('requirements', sa.Column('release_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_requirements_release_id'), 'requirements', ['release_id'], unique=False)
    op.create_foreign_key('fk_requirements_release_id', 'requirements', 'releases', ['release_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_requirements_release_id', 'requirements', type_='foreignkey')
    op.drop_index(op.f('ix_requirements_release_id'), table_name='requirements')
    op.drop_column('requirements', 'release_id')

    op.drop_constraint('fk_executions_test_cycle_id', 'executions', type_='foreignkey')
    op.drop_index(op.f('ix_executions_test_cycle_id'), table_name='executions')
    op.drop_column('executions', 'jira_bug_url')
    op.drop_column('executions', 'jira_bug_id')
    op.drop_column('executions', 'retest_pending_candidate')
    op.drop_column('executions', 'suggest_retry')
    op.drop_column('executions', 'root_cause_summary')
    op.drop_column('executions', 'failure_category')
    op.drop_column('executions', 'test_cycle_id')

    op.drop_index(op.f('ix_test_cycles_release_id'), table_name='test_cycles')
    op.drop_table('test_cycles')
    op.drop_index(op.f('ix_releases_project_id'), table_name='releases')
    op.drop_table('releases')
