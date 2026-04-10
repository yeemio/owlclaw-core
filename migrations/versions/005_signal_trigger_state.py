"""Add signal trigger state persistence tables.

Revision ID: 005_signal_state
Revises: 004_webhook
Create Date: 2026-02-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "005_signal_state"
down_revision: str | None = "004_webhook"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_agents_tenant_agent", "agents", ["tenant_id", "agent_id"], unique=True)
    op.create_index("idx_agents_tenant_paused", "agents", ["tenant_id", "paused"])
    op.create_index(op.f("ix_agents_tenant_id"), "agents", ["tenant_id"])

    op.create_table(
        "pending_instructions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("operator", sa.String(255), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_pending_instr_tenant_agent_expires",
        "pending_instructions",
        ["tenant_id", "agent_id", "expires_at"],
    )
    op.create_index("idx_pending_instr_tenant_consumed", "pending_instructions", ["tenant_id", "consumed"])
    op.create_index("idx_pending_instr_tenant_created", "pending_instructions", ["tenant_id", "created_at"])
    op.create_index(op.f("ix_pending_instructions_tenant_id"), "pending_instructions", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_pending_instructions_tenant_id"), table_name="pending_instructions")
    op.drop_index("idx_pending_instr_tenant_created", table_name="pending_instructions")
    op.drop_index("idx_pending_instr_tenant_consumed", table_name="pending_instructions")
    op.drop_index("idx_pending_instr_tenant_agent_expires", table_name="pending_instructions")
    op.drop_table("pending_instructions")

    op.drop_index(op.f("ix_agents_tenant_id"), table_name="agents")
    op.drop_index("idx_agents_tenant_paused", table_name="agents")
    op.drop_index("idx_agents_tenant_agent", table_name="agents")
    op.drop_table("agents")
