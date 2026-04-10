"""Skill quality snapshot storage (in-memory and SQLAlchemy)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, Float, Index, String, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from owlclaw.db import Base


@dataclass
class SkillQualitySnapshot:
    """Stored quality snapshot."""

    tenant_id: str
    skill_name: str
    window_start: datetime
    window_end: datetime
    metrics: dict[str, Any]
    quality_score: float
    computed_at: datetime


class SkillQualitySnapshotORM(Base):
    """Quality snapshot ORM model for persistent storage."""

    __tablename__ = "skill_quality_snapshots"
    __table_args__ = (
        Index("idx_quality_tenant_skill_computed", "tenant_id", "skill_name", "computed_at"),
        Index("idx_quality_tenant_score", "tenant_id", "quality_score"),
        Index("idx_quality_tenant_skill_name", "tenant_id", "skill_name"),
        Index("idx_quality_tenant_computed", "tenant_id", "computed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_name: Mapped[str] = mapped_column(String(255), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class InMemoryQualityStore:
    """Store quality snapshots in process memory (Lite Mode)."""

    def __init__(self) -> None:
        self._snapshots: list[SkillQualitySnapshot] = []

    def save(self, snapshot: SkillQualitySnapshot) -> None:
        self._snapshots.append(snapshot)

    def list_for_skill(self, *, tenant_id: str, skill_name: str) -> list[SkillQualitySnapshot]:
        out = [s for s in self._snapshots if s.tenant_id == tenant_id and s.skill_name == skill_name]
        out.sort(key=lambda s: s.computed_at)
        return out

    def latest_for_skill(self, *, tenant_id: str, skill_name: str) -> SkillQualitySnapshot | None:
        rows = self.list_for_skill(tenant_id=tenant_id, skill_name=skill_name)
        return rows[-1] if rows else None

    def all_latest(self, *, tenant_id: str) -> list[SkillQualitySnapshot]:
        by_skill: dict[str, SkillQualitySnapshot] = {}
        for item in self._snapshots:
            if item.tenant_id != tenant_id:
                continue
            prev = by_skill.get(item.skill_name)
            if prev is None or item.computed_at > prev.computed_at:
                by_skill[item.skill_name] = item
        return sorted(by_skill.values(), key=lambda s: s.skill_name)


class SQLQualityStore:
    """Store quality snapshots using SQLAlchemy async sessions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, snapshot: SkillQualitySnapshot) -> None:
        row = SkillQualitySnapshotORM(
            tenant_id=snapshot.tenant_id,
            skill_name=snapshot.skill_name,
            window_start=snapshot.window_start,
            window_end=snapshot.window_end,
            metrics_json=snapshot.metrics,
            quality_score=snapshot.quality_score,
            computed_at=_to_utc(snapshot.computed_at),
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()

    async def list_for_skill(self, *, tenant_id: str, skill_name: str) -> list[SkillQualitySnapshot]:
        async with self._session_factory() as session:
            stmt = (
                select(SkillQualitySnapshotORM)
                .where(SkillQualitySnapshotORM.tenant_id == tenant_id)
                .where(SkillQualitySnapshotORM.skill_name == skill_name)
                .order_by(SkillQualitySnapshotORM.computed_at.asc())
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [_from_orm(row) for row in rows]

    async def latest_for_skill(self, *, tenant_id: str, skill_name: str) -> SkillQualitySnapshot | None:
        async with self._session_factory() as session:
            stmt = (
                select(SkillQualitySnapshotORM)
                .where(SkillQualitySnapshotORM.tenant_id == tenant_id)
                .where(SkillQualitySnapshotORM.skill_name == skill_name)
                .order_by(SkillQualitySnapshotORM.computed_at.desc())
                .limit(1)
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _from_orm(row) if row is not None else None


def _from_orm(row: SkillQualitySnapshotORM) -> SkillQualitySnapshot:
    return SkillQualitySnapshot(
        tenant_id=row.tenant_id,
        skill_name=row.skill_name,
        window_start=_to_utc(row.window_start),
        window_end=_to_utc(row.window_end),
        metrics=dict(row.metrics_json or {}),
        quality_score=float(row.quality_score),
        computed_at=_to_utc(row.computed_at),
    )


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
