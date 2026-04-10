"""Add ledger_records table for governance execution logging.

Revision ID: 002_ledger
Revises: 001_initial
Create Date: 2026-02-11

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "002_ledger"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(64),
            nullable=False,
            server_default="default",
        ),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column("run_id", sa.String(255), nullable=False),
        sa.Column("capability_name", sa.String(255), nullable=False),
        sa.Column("task_type", sa.String(100), nullable=False),
        sa.Column("input_params", JSONB, nullable=False),
        sa.Column("output_result", JSONB, nullable=True),
        sa.Column("decision_reasoning", sa.Text(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=False),
        sa.Column("llm_model", sa.String(100), nullable=False),
        sa.Column("llm_tokens_input", sa.Integer(), nullable=False),
        sa.Column("llm_tokens_output", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.DECIMAL(10, 4), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        comment="Agent capability execution records for audit and cost analysis",
    )
    op.create_index(
        "idx_ledger_tenant_agent",
        "ledger_records",
        ["tenant_id", "agent_id"],
    )
    op.create_index(
        "idx_ledger_tenant_capability",
        "ledger_records",
        ["tenant_id", "capability_name"],
    )
    op.create_index(
        "idx_ledger_tenant_created",
        "ledger_records",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        op.f("ix_ledger_records_agent_id"),
        "ledger_records",
        ["agent_id"],
    )
    op.create_index(
        op.f("ix_ledger_records_capability_name"),
        "ledger_records",
        ["capability_name"],
    )
    op.create_index(
        op.f("ix_ledger_records_created_at"),
        "ledger_records",
        ["created_at"],
    )
    op.create_index(
        op.f("ix_ledger_records_run_id"),
        "ledger_records",
        ["run_id"],
    )
    op.create_index(
        op.f("ix_ledger_records_status"),
        "ledger_records",
        ["status"],
    )
    op.create_index(
        op.f("ix_ledger_records_task_type"),
        "ledger_records",
        ["task_type"],
    )
    op.create_index(
        op.f("ix_ledger_records_tenant_id"),
        "ledger_records",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ledger_records_tenant_id"), "ledger_records")
    op.drop_index(op.f("ix_ledger_records_task_type"), "ledger_records")
    op.drop_index(op.f("ix_ledger_records_status"), "ledger_records")
    op.drop_index(op.f("ix_ledger_records_run_id"), "ledger_records")
    op.drop_index(op.f("ix_ledger_records_created_at"), "ledger_records")
    op.drop_index(op.f("ix_ledger_records_capability_name"), "ledger_records")
    op.drop_index(op.f("ix_ledger_records_agent_id"), "ledger_records")
    op.drop_index("idx_ledger_tenant_created", "ledger_records")
    op.drop_index("idx_ledger_tenant_capability", "ledger_records")
    op.drop_index("idx_ledger_tenant_agent", "ledger_records")
    op.drop_table("ledger_records")
