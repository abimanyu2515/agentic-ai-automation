"""Add log analyser fields to executions.

Adds the evidence the upgraded ExecutionAnalysisAgent (LogAnalysisService)
produces, plus trace_analysis and screenshot_findings, which the model had but
PostgreSQL never stored. All nullable, so earlier runs stay valid.

Revision ID: b7e2c4f91a30
Revises: a9d3f6b21c58
Create Date: 2026-11-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e2c4f91a30"
down_revision: Union[str, Sequence[str], None] = "a9d3f6b21c58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("trace_analysis", sa.Text()),
    ("screenshot_findings", sa.Text()),
    ("failing_step", sa.Text()),
    ("evidence", sa.JSON()),
    ("confidence", sa.Float()),
    ("flaky_score", sa.Float()),
    ("frontend_error_detected", sa.Boolean()),
    ("frontend_error_details", sa.Text()),
    ("run_history_summary", sa.JSON()),
)


def upgrade() -> None:
    for name, column_type in _COLUMNS:
        op.add_column("executions", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("executions", name)
