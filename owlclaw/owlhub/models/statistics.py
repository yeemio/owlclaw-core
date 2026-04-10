"""SQLAlchemy model for OwlHub skill statistics."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from owlclaw.db import Base


class SkillStatistics(Base):
    """Aggregated statistics for one logical skill."""

    __tablename__ = "skill_statistics"
    __table_args__ = (
        UniqueConstraint("tenant_id", "skill_id", name="uq_skill_statistics_tenant_skill"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), nullable=False)
    total_downloads: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_installs: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    downloads_last_30d: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_download_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_install_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    skill = relationship("Skill", back_populates="statistics")
