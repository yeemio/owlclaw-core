"""Merge Phase 12 migration heads into a single linear chain.

Revision ID: 011_merge_phase12_heads
Revises: 009_webhook_auth_token_hash, 010_quality_tenant_indexes
Create Date: 2026-03-04
"""

from collections.abc import Sequence


revision: str = "011_merge_phase12_heads"
down_revision: tuple[str, str] = ("009_webhook_auth_token_hash", "010_quality_tenant_indexes")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op merge revision."""


def downgrade() -> None:
    """No-op merge revision."""
