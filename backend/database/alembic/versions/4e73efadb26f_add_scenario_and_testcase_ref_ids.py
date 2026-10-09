"""add_scenario_and_testcase_ref_ids

Revision ID: 4e73efadb26f
Revises: 33e7bfda0aab
Create Date: 2026-07-18 16:19:02.212294

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e73efadb26f'
down_revision: Union[str, Sequence[str], None] = '33e7bfda0aab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add scenario_ref_id column to scenarios table
    op.add_column('scenarios', sa.Column('scenario_ref_id', sa.String(20), nullable=True))
    op.create_index('ix_scenarios_scenario_ref_id', 'scenarios', ['scenario_ref_id'])
    
    # Add test_case_ref_id column to test_cases table
    op.add_column('test_cases', sa.Column('test_case_ref_id', sa.String(30), nullable=True))
    op.create_index('ix_test_cases_test_case_ref_id', 'test_cases', ['test_case_ref_id'])


def downgrade() -> None:
    """Downgrade schema."""
    # Remove indexes and columns
    op.drop_index('ix_test_cases_test_case_ref_id', 'test_cases')
    op.drop_column('test_cases', 'test_case_ref_id')
    
    op.drop_index('ix_scenarios_scenario_ref_id', 'scenarios')
    op.drop_column('scenarios', 'scenario_ref_id')
