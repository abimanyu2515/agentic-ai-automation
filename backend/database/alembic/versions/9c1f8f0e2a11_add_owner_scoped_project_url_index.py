"""Add owner scoped normalized project URL uniqueness.

Revision ID: 9c1f8f0e2a11
Revises: 4e73efadb26f
Create Date: 2026-10-13 12:00:00.000000
"""

from typing import Sequence, Union
from urllib.parse import urlsplit, urlunsplit

from alembic import op
import sqlalchemy as sa


revision: str = "9c1f8f0e2a11"
down_revision: Union[str, Sequence[str], None] = "4e73efadb26f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalize_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("unsupported scheme")
    if not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("absolute URL with no credentials or fragment required")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("host required")
    port = parsed.port
    scheme = parsed.scheme.lower()
    netloc = hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
    path = "" if parsed.path == "/" else parsed.path
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, owner_id, target_url FROM projects "
            "WHERE is_deleted = false AND owner_id IS NOT NULL AND target_url IS NOT NULL"
        )
    ).mappings().all()

    invalid: list[str] = []
    identities: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    for row in rows:
        try:
            normalized = _normalize_url(row["target_url"])
        except ValueError as exc:
            invalid.append(f"{row['id']}: {exc}")
            continue
        key = (str(row["owner_id"]), normalized)
        if key in identities:
            duplicates.append(f"{identities[key]} and {row['id']} for {normalized}")
        else:
            identities[key] = str(row["id"])

    if invalid or duplicates:
        details = []
        if invalid:
            details.append("invalid target URLs: " + "; ".join(invalid[:20]))
        if duplicates:
            details.append("duplicate owner scoped URLs: " + "; ".join(duplicates[:20]))
        raise RuntimeError(
            "Project URL uniqueness migration stopped. Resolve the reported conflicts before retrying. "
            + " | ".join(details)
        )

    op.create_index(
        "uq_projects_owner_target_url",
        "projects",
        ["owner_id", "target_url"],
        unique=True,
        postgresql_where=sa.text(
            "owner_id IS NOT NULL AND target_url IS NOT NULL AND is_deleted = false"
        ),
        sqlite_where=sa.text(
            "owner_id IS NOT NULL AND target_url IS NOT NULL AND is_deleted = 0"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_projects_owner_target_url", table_name="projects")
