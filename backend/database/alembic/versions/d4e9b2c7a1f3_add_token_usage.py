"""Add the token_usage table (Token Utilization feature).

Revision ID: d4e9b2c7a1f3
Revises: c3f8a1d2e905
Create Date: 2026-11-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d4e9b2c7a1f3"
down_revision: Union[str, Sequence[str], None] = "c3f8a1d2e905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "token_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(200), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requirement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("test_case_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(50), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False, server_default=""),
        sa.Column("model", sa.String(200), nullable=False, server_default=""),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("output_cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("priced", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("price_source", sa.String(20), nullable=False, server_default="none"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="success"),
        sa.Column("error_type", sa.String(200), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_token_usage_created_at", "token_usage", ["created_at"])
    op.create_index("ix_token_usage_agent", "token_usage", ["agent"])
    op.create_index("ix_token_usage_model", "token_usage", ["model"])
    op.create_index("ix_token_usage_run_id", "token_usage", ["run_id"])
    op.create_index("ix_token_usage_project_created", "token_usage", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_token_usage_project_created", table_name="token_usage")
    op.drop_index("ix_token_usage_run_id", table_name="token_usage")
    op.drop_index("ix_token_usage_model", table_name="token_usage")
    op.drop_index("ix_token_usage_agent", table_name="token_usage")
    op.drop_index("ix_token_usage_created_at", table_name="token_usage")
    op.drop_table("token_usage")
