"""Harden ledger indexes and webhook idempotency primary key.

Revision ID: 009_governance_hardening
Revises: 008_ledger_runtime_metadata
Create Date: 2026-03-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "009_governance_hardening"
down_revision: str | None = "008_ledger_runtime_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Ledger indexes: ensure tenant-prefixed lookup indexes.
    op.drop_index(op.f("ix_ledger_records_agent_id"), table_name="ledger_records")
    op.drop_index(op.f("ix_ledger_records_capability_name"), table_name="ledger_records")
    op.drop_index(op.f("ix_ledger_records_created_at"), table_name="ledger_records")
    op.drop_index(op.f("ix_ledger_records_run_id"), table_name="ledger_records")
    op.drop_index(op.f("ix_ledger_records_status"), table_name="ledger_records")
    op.drop_index(op.f("ix_ledger_records_task_type"), table_name="ledger_records")
    op.drop_index(op.f("ix_ledger_records_execution_mode"), table_name="ledger_records")

    op.create_index("idx_ledger_tenant_run", "ledger_records", ["tenant_id", "run_id"])
    op.create_index("idx_ledger_tenant_status", "ledger_records", ["tenant_id", "status"])
    op.create_index("idx_ledger_tenant_task_type", "ledger_records", ["tenant_id", "task_type"])
    op.create_index("idx_ledger_tenant_execution_mode", "ledger_records", ["tenant_id", "execution_mode"])

    # Webhook idempotency keys: UUID primary key + unique idempotency key.
    op.add_column(
        "webhook_idempotency_keys",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.drop_constraint(
        "webhook_idempotency_keys_pkey",
        "webhook_idempotency_keys",
        type_="primary",
    )
    op.create_primary_key(
        "pk_webhook_idempotency_keys",
        "webhook_idempotency_keys",
        ["id"],
    )
    op.create_unique_constraint(
        "uq_webhook_idempotency_keys_key",
        "webhook_idempotency_keys",
        ["key"],
    )
    op.create_index(
        "idx_webhook_idempotency_tenant_key",
        "webhook_idempotency_keys",
        ["tenant_id", "key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_webhook_idempotency_tenant_key", table_name="webhook_idempotency_keys")
    op.drop_constraint("uq_webhook_idempotency_keys_key", "webhook_idempotency_keys", type_="unique")
    op.drop_constraint("pk_webhook_idempotency_keys", "webhook_idempotency_keys", type_="primary")
    op.create_primary_key("webhook_idempotency_keys_pkey", "webhook_idempotency_keys", ["key"])
    op.drop_column("webhook_idempotency_keys", "id")

    op.drop_index("idx_ledger_tenant_execution_mode", table_name="ledger_records")
    op.drop_index("idx_ledger_tenant_task_type", table_name="ledger_records")
    op.drop_index("idx_ledger_tenant_status", table_name="ledger_records")
    op.drop_index("idx_ledger_tenant_run", table_name="ledger_records")

    op.create_index(op.f("ix_ledger_records_execution_mode"), "ledger_records", ["execution_mode"])
    op.create_index(op.f("ix_ledger_records_task_type"), "ledger_records", ["task_type"])
    op.create_index(op.f("ix_ledger_records_status"), "ledger_records", ["status"])
    op.create_index(op.f("ix_ledger_records_run_id"), "ledger_records", ["run_id"])
    op.create_index(op.f("ix_ledger_records_created_at"), "ledger_records", ["created_at"])
    op.create_index(op.f("ix_ledger_records_capability_name"), "ledger_records", ["capability_name"])
    op.create_index(op.f("ix_ledger_records_agent_id"), "ledger_records", ["agent_id"])
