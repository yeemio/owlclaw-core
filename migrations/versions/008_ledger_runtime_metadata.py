"""Add runtime metadata columns to ledger_records.

Revision ID: 008_ledger_runtime_metadata
Revises: 007_skill_quality_snapshots
Create Date: 2026-02-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008_ledger_runtime_metadata"
down_revision: str | None = "007_skill_quality_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ledger_records", sa.Column("migration_weight", sa.Integer(), nullable=True))
    op.add_column("ledger_records", sa.Column("execution_mode", sa.String(length=32), nullable=True))
    op.add_column("ledger_records", sa.Column("risk_level", sa.DECIMAL(5, 4), nullable=True))
    op.add_column("ledger_records", sa.Column("approval_by", sa.String(length=255), nullable=True))
    op.add_column("ledger_records", sa.Column("approval_time", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_ledger_records_execution_mode"), "ledger_records", ["execution_mode"])


def downgrade() -> None:
    op.drop_index(op.f("ix_ledger_records_execution_mode"), table_name="ledger_records")
    op.drop_column("ledger_records", "approval_time")
    op.drop_column("ledger_records", "approval_by")
    op.drop_column("ledger_records", "risk_level")
    op.drop_column("ledger_records", "execution_mode")
    op.drop_column("ledger_records", "migration_weight")
