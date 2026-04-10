"""ORM models for signal trigger state persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from owlclaw.db import Base


class AgentControlStateORM(Base):
    """Persistent agent control state (pause/resume)."""

    __tablename__ = "agents"
    __table_args__ = (
        Index("idx_agents_tenant_agent", "tenant_id", "agent_id", unique=True),
        Index("idx_agents_tenant_paused", "tenant_id", "paused"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=func.now(),
    )


class PendingInstructionORM(Base):
    """Persistent pending instructions injected into next agent run."""

    __tablename__ = "pending_instructions"
    __table_args__ = (
        Index("idx_pending_instr_tenant_agent_expires", "tenant_id", "agent_id", "expires_at"),
        Index("idx_pending_instr_tenant_consumed", "tenant_id", "consumed"),
        Index("idx_pending_instr_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    operator: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
