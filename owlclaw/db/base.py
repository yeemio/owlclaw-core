"""Declarative base and tenant_id mixin for OwlClaw ORM models."""

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all OwlClaw ORM models.

    Ensures every table has tenant_id from day one (self-hosted default
    'default'; Cloud will use real tenant IDs). Exposes metadata for Alembic.
    """

    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="default",
        index=True,
        doc="Tenant identifier; self-hosted default is 'default'.",
    )
