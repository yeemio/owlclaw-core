"""ORM models for webhook trigger persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from owlclaw.db import Base


class WebhookEndpointModel(Base):
    """Webhook endpoint configuration."""

    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        Index("idx_webhook_endpoints_tenant_target_agent", "tenant_id", "target_agent_id"),
        Index("idx_webhook_endpoints_tenant_enabled", "tenant_id", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    auth_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    target_agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_method: Mapped[dict] = mapped_column(JSONB, nullable=False)
    transformation_rule_id: Mapped[UUID | None] = mapped_column(nullable=True)
    execution_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="async")
    timeout: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_policy: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WebhookEventModel(Base):
    """Webhook request/validation/transformation/execution logs."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        Index("idx_webhook_events_tenant_endpoint_timestamp", "tenant_id", "endpoint_id", "timestamp"),
        Index("idx_webhook_events_tenant_request_id", "tenant_id", "request_id"),
        Index("idx_webhook_events_tenant_timestamp", "tenant_id", "timestamp"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    endpoint_id: Mapped[UUID] = mapped_column(ForeignKey("webhook_endpoints.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class WebhookIdempotencyKeyModel(Base):
    """Processed idempotency keys and cached execution result."""

    __tablename__ = "webhook_idempotency_keys"
    __table_args__ = (
        Index("idx_webhook_idempotency_tenant_endpoint", "tenant_id", "endpoint_id"),
        Index("idx_webhook_idempotency_tenant_expires", "tenant_id", "expires_at"),
        Index("idx_webhook_idempotency_tenant_key", "tenant_id", "key", unique=True),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    endpoint_id: Mapped[UUID] = mapped_column(ForeignKey("webhook_endpoints.id"), nullable=False)
    execution_id: Mapped[UUID] = mapped_column(nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookTransformationRuleModel(Base):
    """Transformation rules from inbound payload to agent input."""

    __tablename__ = "webhook_transformation_rules"
    __table_args__ = (Index("idx_webhook_rules_tenant_name", "tenant_id", "name"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    target_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    mappings: Mapped[list] = mapped_column(JSONB, nullable=False)
    custom_logic: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class WebhookExecutionModel(Base):
    """Execution records for webhook-triggered agent runs."""

    __tablename__ = "webhook_executions"
    __table_args__ = (
        Index("idx_webhook_executions_tenant_endpoint_started", "tenant_id", "endpoint_id", "started_at"),
        Index("idx_webhook_executions_tenant_agent_started", "tenant_id", "agent_id", "started_at"),
        Index("idx_webhook_executions_tenant_status", "tenant_id", "status"),
        Index("idx_webhook_executions_tenant_idempotency", "tenant_id", "idempotency_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    endpoint_id: Mapped[UUID] = mapped_column(ForeignKey("webhook_endpoints.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    input: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
