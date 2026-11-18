"""Mark documents created before real uploads as failed (spec 0006, slice 2, AC-9).

A document uploaded since spec 0006 is saved under its own <document_id>
folder, so its storage_key holds its ID. Older documents were created from a
file name only and never had a file, so they become failed with a message
asking the user to upload them again. Their records stay visible.

Revision ID: a3d7f1c9e526
Revises: f2b6c8d41a07
Create Date: 2026-11-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a3d7f1c9e526"
down_revision: Union[str, Sequence[str], None] = "f2b6c8d41a07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_DOCUMENT_ERROR = "file was never uploaded, please upload it again"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE documents SET embedding_status = 'failed', error = :error "
            "WHERE position(CAST(id AS TEXT) IN COALESCE(storage_key, '')) = 0"
        ),
        {"error": OLD_DOCUMENT_ERROR},
    )


def downgrade() -> None:
    # A data only change: the old status was never meaningful, so nothing is restored.
    pass
