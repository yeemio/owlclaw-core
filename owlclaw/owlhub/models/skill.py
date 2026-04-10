"""SQLAlchemy model for OwlHub skill registry entries."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from owlclaw.db import Base


class Skill(Base):
    """One logical skill identity scoped by tenant/publisher/name."""

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "publisher", "name", name="uq_skills_tenant_publisher_name"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    publisher: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    license: Mapped[str] = mapped_column(String(64), nullable=False)
    repository: Mapped[str | None] = mapped_column(String(255), nullable=True)
    homepage: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="released")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, default=list)
    taken_down: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    takedown_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    takedown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    versions = relationship("SkillVersion", back_populates="skill", cascade="all, delete-orphan")
    statistics = relationship("SkillStatistics", back_populates="skill", uselist=False, cascade="all, delete-orphan")
    reviews = relationship("ReviewRecord", back_populates="skill", cascade="all, delete-orphan")
