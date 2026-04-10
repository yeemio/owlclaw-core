"""Harden skill_quality_snapshots indexes with tenant-prefixed variants.

Revision ID: 010_quality_tenant_indexes
Revises: 009_governance_hardening
Create Date: 2026-03-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "010_quality_tenant_indexes"
down_revision: str | None = "009_governance_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_skill_quality_snapshots_skill_name"), table_name="skill_quality_snapshots")
    op.drop_index(op.f("ix_skill_quality_snapshots_computed_at"), table_name="skill_quality_snapshots")
    op.create_index(
        "idx_quality_tenant_skill_name",
        "skill_quality_snapshots",
        ["tenant_id", "skill_name"],
    )
    op.create_index(
        "idx_quality_tenant_computed",
        "skill_quality_snapshots",
        ["tenant_id", "computed_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_quality_tenant_computed", table_name="skill_quality_snapshots")
    op.drop_index("idx_quality_tenant_skill_name", table_name="skill_quality_snapshots")
    op.create_index(op.f("ix_skill_quality_snapshots_computed_at"), "skill_quality_snapshots", ["computed_at"])
    op.create_index(op.f("ix_skill_quality_snapshots_skill_name"), "skill_quality_snapshots", ["skill_name"])
