"""Initial schema (placeholder). No OwlClaw tables yet; Ledger/Memory add later.

Revision ID: 001_initial
Revises:
Create Date: 2026-02-11

"""
from collections.abc import Sequence

revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No tables in this revision; alembic_version is created by Alembic."""
    pass


def downgrade() -> None:
    """Nothing to drop."""
    pass
