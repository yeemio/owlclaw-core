"""Add webhook trigger persistence tables.

Revision ID: 004_webhook
Revises: 003_memory
Create Date: 2026-02-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "004_webhook"
down_revision: str | None = "003_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "webhook_endpoints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("auth_token", sa.String(255), nullable=False),
        sa.Column("target_agent_id", sa.String(255), nullable=False),
        sa.Column("auth_method", JSONB, nullable=False),
        sa.Column("transformation_rule_id", UUID(as_uuid=True), nullable=True),
        sa.Column("execution_mode", sa.String(10), nullable=False, server_default="async"),
        sa.Column("timeout", sa.Integer(), nullable=True),
        sa.Column("retry_policy", JSONB, nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64), nullable=True),
    )
    op.create_index("idx_webhook_endpoints_tenant_target_agent", "webhook_endpoints", ["tenant_id", "target_agent_id"])
    op.create_index("idx_webhook_endpoints_tenant_enabled", "webhook_endpoints", ["tenant_id", "enabled"])
    op.create_index(op.f("ix_webhook_endpoints_tenant_id"), "webhook_endpoints", ["tenant_id"])

    op.create_table(
        "webhook_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("endpoint_id", UUID(as_uuid=True), sa.ForeignKey("webhook_endpoints.id"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        sa.Column("data", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
    )
    op.create_index("idx_webhook_events_tenant_endpoint_timestamp", "webhook_events", ["tenant_id", "endpoint_id", "timestamp"])
    op.create_index("idx_webhook_events_tenant_request_id", "webhook_events", ["tenant_id", "request_id"])
    op.create_index("idx_webhook_events_tenant_timestamp", "webhook_events", ["tenant_id", "timestamp"])
    op.create_index(op.f("ix_webhook_events_tenant_id"), "webhook_events", ["tenant_id"])

    op.create_table(
        "webhook_idempotency_keys",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("endpoint_id", UUID(as_uuid=True), sa.ForeignKey("webhook_endpoints.id"), nullable=False),
        sa.Column("execution_id", UUID(as_uuid=True), nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_webhook_idempotency_tenant_endpoint", "webhook_idempotency_keys", ["tenant_id", "endpoint_id"])
    op.create_index("idx_webhook_idempotency_tenant_expires", "webhook_idempotency_keys", ["tenant_id", "expires_at"])
    op.create_index(op.f("ix_webhook_idempotency_keys_tenant_id"), "webhook_idempotency_keys", ["tenant_id"])

    op.create_table(
        "webhook_transformation_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_schema", JSONB, nullable=True),
        sa.Column("target_schema", JSONB, nullable=False),
        sa.Column("mappings", JSONB, nullable=False),
        sa.Column("custom_logic", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_webhook_rules_tenant_name", "webhook_transformation_rules", ["tenant_id", "name"])
    op.create_index(op.f("ix_webhook_transformation_rules_tenant_id"), "webhook_transformation_rules", ["tenant_id"])

    op.create_table(
        "webhook_executions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("endpoint_id", UUID(as_uuid=True), sa.ForeignKey("webhook_endpoints.id"), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input", JSONB, nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("error", JSONB, nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("idx_webhook_executions_tenant_endpoint_started", "webhook_executions", ["tenant_id", "endpoint_id", "started_at"])
    op.create_index("idx_webhook_executions_tenant_agent_started", "webhook_executions", ["tenant_id", "agent_id", "started_at"])
    op.create_index("idx_webhook_executions_tenant_status", "webhook_executions", ["tenant_id", "status"])
    op.create_index("idx_webhook_executions_tenant_idempotency", "webhook_executions", ["tenant_id", "idempotency_key"])
    op.create_index(op.f("ix_webhook_executions_tenant_id"), "webhook_executions", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_webhook_executions_tenant_id"), table_name="webhook_executions")
    op.drop_index("idx_webhook_executions_tenant_idempotency", table_name="webhook_executions")
    op.drop_index("idx_webhook_executions_tenant_status", table_name="webhook_executions")
    op.drop_index("idx_webhook_executions_tenant_agent_started", table_name="webhook_executions")
    op.drop_index("idx_webhook_executions_tenant_endpoint_started", table_name="webhook_executions")
    op.drop_table("webhook_executions")

    op.drop_index(op.f("ix_webhook_transformation_rules_tenant_id"), table_name="webhook_transformation_rules")
    op.drop_index("idx_webhook_rules_tenant_name", table_name="webhook_transformation_rules")
    op.drop_table("webhook_transformation_rules")

    op.drop_index(op.f("ix_webhook_idempotency_keys_tenant_id"), table_name="webhook_idempotency_keys")
    op.drop_index("idx_webhook_idempotency_tenant_expires", table_name="webhook_idempotency_keys")
    op.drop_index("idx_webhook_idempotency_tenant_endpoint", table_name="webhook_idempotency_keys")
    op.drop_table("webhook_idempotency_keys")

    op.drop_index(op.f("ix_webhook_events_tenant_id"), table_name="webhook_events")
    op.drop_index("idx_webhook_events_tenant_timestamp", table_name="webhook_events")
    op.drop_index("idx_webhook_events_tenant_request_id", table_name="webhook_events")
    op.drop_index("idx_webhook_events_tenant_endpoint_timestamp", table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_index(op.f("ix_webhook_endpoints_tenant_id"), table_name="webhook_endpoints")
    op.drop_index("idx_webhook_endpoints_tenant_enabled", table_name="webhook_endpoints")
    op.drop_index("idx_webhook_endpoints_tenant_target_agent", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
