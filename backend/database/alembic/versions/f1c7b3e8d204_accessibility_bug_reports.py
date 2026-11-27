"""Let bug reports come from accessibility findings.

Spec 0011, AC-6 and AC-7: an accessibility bug from a standalone scan has no
run and no test case, so `execution_id` and `test_case_id` become nullable.
Existing rows keep their values; this only relaxes nullability.

Adds `accessibility_report_id` and a `failure_category` lookup column
(backfilled from the payload), a check constraint that keeps at least one
source on every row, and the `(project_id, failure_category, error_signature)`
index for the one open accessibility bug per signature lookup.

Revision ID: f1c7b3e8d204
Revises: a7d3e9b1c540
Create Date: 2026-11-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f1c7b3e8d204"
down_revision: Union[str, Sequence[str], None] = "a7d3e9b1c540"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("bug_reports", "execution_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.alter_column("bug_reports", "test_case_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)
    op.add_column("bug_reports", sa.Column("accessibility_report_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("bug_reports", sa.Column("failure_category", sa.String(50), nullable=True))
    op.execute(
        "UPDATE bug_reports SET failure_category = LEFT(payload::jsonb ->> 'failure_category', 50) "
        "WHERE failure_category IS NULL"
    )
    op.create_check_constraint(
        "ck_bug_reports_has_source",
        "bug_reports",
        "execution_id IS NOT NULL OR accessibility_report_id IS NOT NULL",
    )
    op.create_index(
        "ix_bug_reports_category_signature",
        "bug_reports",
        ["project_id", "failure_category", "error_signature"],
    )


def downgrade() -> None:
    op.drop_index("ix_bug_reports_category_signature", table_name="bug_reports")
    op.drop_constraint("ck_bug_reports_has_source", "bug_reports", type_="check")
    # Rows without a run cannot satisfy NOT NULL again; remove them first.
    op.execute("DELETE FROM bug_reports WHERE execution_id IS NULL OR test_case_id IS NULL")
    op.drop_column("bug_reports", "failure_category")
    op.drop_column("bug_reports", "accessibility_report_id")
    op.alter_column("bug_reports", "test_case_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.alter_column("bug_reports", "execution_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
