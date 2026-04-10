"""Store webhook endpoint auth token as hash.

Revision ID: 009_webhook_auth_token_hash
Revises: 008_ledger_runtime_metadata
Create Date: 2026-03-03
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "009_webhook_auth_token_hash"
down_revision: str | None = "008_ledger_runtime_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("webhook_endpoints", sa.Column("auth_token_hash", sa.String(length=255), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, auth_token FROM webhook_endpoints")).fetchall()
    for row in rows:
        raw_token = str(row.auth_token or "")
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        bind.execute(
            sa.text("UPDATE webhook_endpoints SET auth_token_hash = :token_hash WHERE id = :endpoint_id"),
            {"token_hash": token_hash, "endpoint_id": row.id},
        )
    op.alter_column("webhook_endpoints", "auth_token_hash", nullable=False)
    op.drop_column("webhook_endpoints", "auth_token")


def downgrade() -> None:
    op.add_column("webhook_endpoints", sa.Column("auth_token", sa.String(length=255), nullable=True))
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE webhook_endpoints SET auth_token = auth_token_hash"))
    op.alter_column("webhook_endpoints", "auth_token", nullable=False)
    op.drop_column("webhook_endpoints", "auth_token_hash")
