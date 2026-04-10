"""PgVector-backed MemoryStore implementation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import Boolean, DateTime, Integer, String, Text, and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from owlclaw.agent.memory.decay import time_decay
from owlclaw.agent.memory.models import MemoryEntry, SecurityLevel
from owlclaw.agent.memory.store import MemoryStore
from owlclaw.db import Base


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class MemoryEntryORM(Base):
    """ORM model for memory_entries table (used by PgVectorStore)."""

    __tablename__ = "memory_entries"
    # Tests may temporarily unload/reload this module to simulate optional dependency
    # scenarios; keep table declaration idempotent across repeated imports.
    __table_args__ = {
        "comment": "Agent long-term memory entries (vector + metadata).",
        "extend_existing": True,
    }

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(255), nullable=False, server_default="default", index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    security_level: Mapped[str] = mapped_column(String(20), nullable=False, server_default="internal")
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


def _orm_to_entry(row: MemoryEntryORM) -> MemoryEntry:
    level = SecurityLevel(row.security_level) if row.security_level in {e.value for e in SecurityLevel} else SecurityLevel.INTERNAL
    return MemoryEntry(
        id=row.id,
        agent_id=row.agent_id,
        tenant_id=row.tenant_id,
        content=row.content,
        embedding=row.embedding if hasattr(row, "embedding") and row.embedding is not None else None,
        tags=row.tags or [],
        security_level=level,
        version=row.version,
        created_at=row.created_at,
        accessed_at=row.accessed_at,
        access_count=row.access_count,
        archived=row.archived,
    )


class PgVectorStore(MemoryStore):
    """PostgreSQL + pgvector implementation of MemoryStore."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding_dimensions: int = 1536,
        time_decay_half_life_hours: float = 168.0,
    ) -> None:
        self._session_factory = session_factory
        self._embedding_dimensions = embedding_dimensions
        self._time_decay_half_life_hours = time_decay_half_life_hours
        schema_dimensions = self._schema_embedding_dimensions()
        if schema_dimensions is not None and schema_dimensions != embedding_dimensions:
            raise ValueError(
                "PgVectorStore embedding_dimensions does not match memory_entries.embedding "
                f"schema dimensions ({embedding_dimensions} != {schema_dimensions})"
            )

    @staticmethod
    def _schema_embedding_dimensions() -> int | None:
        vector_type = MemoryEntryORM.__table__.c.embedding.type
        return getattr(vector_type, "dim", None) or getattr(vector_type, "dimensions", None)

    def _validate_embedding(
        self, embedding: list[float] | None, field_name: str, *, allow_none: bool
    ) -> None:
        if embedding is None:
            if allow_none:
                return
            raise ValueError(f"{field_name} must not be None")
        if len(embedding) != self._embedding_dimensions:
            raise ValueError(
                f"{field_name} length must be {self._embedding_dimensions}, got {len(embedding)}"
            )

    async def save(self, entry: MemoryEntry) -> UUID:
        self._validate_embedding(entry.embedding, "entry.embedding", allow_none=True)
        if len(entry.content) > 2000:
            raise ValueError("entry.content length must be <= 2000")
        async with self._session_factory() as session:
            row = MemoryEntryORM(
                id=entry.id,
                agent_id=entry.agent_id,
                tenant_id=entry.tenant_id,
                content=entry.content,
                embedding=entry.embedding,
                tags=entry.tags,
                security_level=entry.security_level.value,
                version=entry.version,
                created_at=entry.created_at,
                accessed_at=entry.accessed_at,
                access_count=entry.access_count,
                archived=entry.archived,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def search(
        self,
        agent_id: str,
        tenant_id: str,
        query_embedding: list[float] | None,
        limit: int = 5,
        tags: list[str] | None = None,
        include_archived: bool = False,
    ) -> list[tuple[MemoryEntry, float]]:
        self._validate_embedding(query_embedding, "query_embedding", allow_none=True)
        async with self._session_factory() as session:
            base_filter = and_(
                MemoryEntryORM.agent_id == agent_id,
                MemoryEntryORM.tenant_id == tenant_id,
            )
            if not include_archived:
                base_filter = and_(base_filter, MemoryEntryORM.archived.is_(False))

            if query_embedding is None:
                q = select(MemoryEntryORM).where(base_filter).order_by(MemoryEntryORM.created_at.desc()).limit(limit * 2)
                if tags:
                    for t in tags:
                        q = q.where(MemoryEntryORM.tags.contains([t]))
                result = await session.execute(q)
                rows = list(result.scalars().all())
                out: list[tuple[MemoryEntry, float]] = []
                for r in rows[:limit]:
                    out.append((_orm_to_entry(r), 1.0))
                return out

            # Vector similarity: select row + cosine distance, then apply time decay
            dist_col = MemoryEntryORM.embedding.cosine_distance(query_embedding).label("cosine_dist")
            q = (
                select(MemoryEntryORM, dist_col)
                .where(base_filter)
                .order_by(dist_col)
                .limit(limit * 5)
            )
            if tags:
                for t in tags:
                    q = q.where(MemoryEntryORM.tags.contains([t]))
            result = await session.execute(q)
            rows_with_dist = list(result.all())
            now = _now_utc()
            scored: list[tuple[MemoryEntry, float]] = []
            for r, cosine_dist in rows_with_dist:
                entry = _orm_to_entry(r)
                # pgvector cosine_distance: 0 = same, 2 = opposite; similarity ≈ 1 - (d/2)
                sim = max(0.0, 1.0 - (cosine_dist or 0) / 2.0)
                created = r.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_hours = (now - created).total_seconds() / 3600.0
                final = sim * time_decay(age_hours, self._time_decay_half_life_hours)
                scored.append((entry, final))
            scored.sort(key=lambda x: -x[1])
            return scored[:limit]

    async def get_recent(
        self,
        agent_id: str,
        tenant_id: str,
        hours: int = 24,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        async with self._session_factory() as session:
            conditions = [
                MemoryEntryORM.agent_id == agent_id,
                MemoryEntryORM.tenant_id == tenant_id,
                MemoryEntryORM.archived.is_(False),
            ]
            if hours > 0:
                cutoff = _now_utc() - timedelta(hours=hours)
                conditions.append(MemoryEntryORM.created_at >= cutoff)
            q = select(MemoryEntryORM).where(and_(*conditions)).order_by(MemoryEntryORM.created_at.desc()).limit(limit)
            result = await session.execute(q)
            return [_orm_to_entry(r) for r in result.scalars().all()]

    async def archive(self, entry_ids: list[UUID]) -> int:
        if not entry_ids:
            return 0
        async with self._session_factory() as session:
            r = await session.execute(
                update(MemoryEntryORM).where(MemoryEntryORM.id.in_(entry_ids)).values(archived=True)
            )
            await session.commit()
            return int(getattr(r, "rowcount", 0) or 0)

    async def delete(self, entry_ids: list[UUID]) -> int:
        if not entry_ids:
            return 0
        async with self._session_factory() as session:
            r = await session.execute(delete(MemoryEntryORM).where(MemoryEntryORM.id.in_(entry_ids)))
            await session.commit()
            return int(getattr(r, "rowcount", 0) or 0)

    async def count(self, agent_id: str, tenant_id: str) -> int:
        async with self._session_factory() as session:
            q = select(func.count()).select_from(MemoryEntryORM).where(
                and_(
                    MemoryEntryORM.agent_id == agent_id,
                    MemoryEntryORM.tenant_id == tenant_id,
                    MemoryEntryORM.archived.is_(False),
                )
            )
            result = await session.execute(q)
            return result.scalar() or 0

    async def update_access(
        self, agent_id: str, tenant_id: str, entry_ids: list[UUID]
    ) -> None:
        if not entry_ids:
            return
        now = _now_utc()
        async with self._session_factory() as session:
            await session.execute(
                update(MemoryEntryORM)
                .where(
                    and_(
                        MemoryEntryORM.id.in_(entry_ids),
                        MemoryEntryORM.agent_id == agent_id,
                        MemoryEntryORM.tenant_id == tenant_id,
                    )
                )
                .values(accessed_at=now, access_count=MemoryEntryORM.access_count + 1)
            )
            await session.commit()

    async def list_entries(
        self,
        agent_id: str,
        tenant_id: str,
        order_created_asc: bool,
        limit: int,
        include_archived: bool = False,
    ) -> list[MemoryEntry]:
        async with self._session_factory() as session:
            base = and_(
                MemoryEntryORM.agent_id == agent_id,
                MemoryEntryORM.tenant_id == tenant_id,
            )
            if not include_archived:
                base = and_(base, MemoryEntryORM.archived.is_(False))
            order = MemoryEntryORM.created_at.asc() if order_created_asc else MemoryEntryORM.created_at.desc()
            q = select(MemoryEntryORM).where(base).order_by(order).limit(limit)
            result = await session.execute(q)
            return [_orm_to_entry(r) for r in result.scalars().all()]

    async def get_expired_entry_ids(
        self,
        agent_id: str,
        tenant_id: str,
        before: datetime,
        max_access_count: int = 0,
    ) -> list[UUID]:
        async with self._session_factory() as session:
            q = (
                select(MemoryEntryORM.id)
                .where(
                    and_(
                        MemoryEntryORM.agent_id == agent_id,
                        MemoryEntryORM.tenant_id == tenant_id,
                        MemoryEntryORM.archived.is_(False),
                        MemoryEntryORM.created_at < before,
                        MemoryEntryORM.access_count <= max_access_count,
                    )
                )
            )
            result = await session.execute(q)
            return [row[0] for row in result.all()]
