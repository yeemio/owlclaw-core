"""Add skill quality snapshots table.

Revision ID: 007_skill_quality_snapshots
Revises: 006_owlhub_core
Create Date: 2026-02-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "007_skill_quality_snapshots"
down_revision: str | None = "006_owlhub_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "skill_quality_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("skill_name", sa.String(255), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_quality_tenant_skill_computed", "skill_quality_snapshots", ["tenant_id", "skill_name", "computed_at"])
    op.create_index("idx_quality_tenant_score", "skill_quality_snapshots", ["tenant_id", "quality_score"])
    op.create_index(op.f("ix_skill_quality_snapshots_tenant_id"), "skill_quality_snapshots", ["tenant_id"])
    op.create_index(op.f("ix_skill_quality_snapshots_skill_name"), "skill_quality_snapshots", ["skill_name"])
    op.create_index(op.f("ix_skill_quality_snapshots_computed_at"), "skill_quality_snapshots", ["computed_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_skill_quality_snapshots_computed_at"), table_name="skill_quality_snapshots")
    op.drop_index(op.f("ix_skill_quality_snapshots_skill_name"), table_name="skill_quality_snapshots")
    op.drop_index(op.f("ix_skill_quality_snapshots_tenant_id"), table_name="skill_quality_snapshots")
    op.drop_index("idx_quality_tenant_score", table_name="skill_quality_snapshots")
    op.drop_index("idx_quality_tenant_skill_computed", table_name="skill_quality_snapshots")
    op.drop_table("skill_quality_snapshots")
