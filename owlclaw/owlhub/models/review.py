"""SQLAlchemy model for OwlHub review records."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from owlclaw.db import Base


class ReviewRecord(Base):
    """Review workflow record for a published skill version."""

    __tablename__ = "review_records"
    __table_args__ = (
        Index("idx_review_records_tenant_publisher", "tenant_id", "publisher"),
        Index("idx_review_records_tenant_status", "tenant_id", "status"),
        Index("idx_review_records_tenant_submitted", "tenant_id", "submitted_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    skill_id: Mapped[UUID | None] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), nullable=True)
    publisher: Mapped[str] = mapped_column(String(120), nullable=False)
    skill_name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    comments: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    skill = relationship("Skill", back_populates="reviews")
