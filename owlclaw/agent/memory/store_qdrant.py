"""Qdrant-backed MemoryStore implementation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

from owlclaw.agent.memory.decay import time_decay
from owlclaw.agent.memory.models import MemoryEntry, SecurityLevel
from owlclaw.agent.memory.store import MemoryStore

try:  # pragma: no cover - import availability depends on environment
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http import models as qmodels
except ImportError:  # pragma: no cover
    AsyncQdrantClient = None  # type: ignore[misc,assignment]
    qmodels = None  # type: ignore[assignment]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw: Any) -> datetime:
    if not raw:
        return _now_utc()
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return _now_utc()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _parse_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    if isinstance(raw, int | float):
        return raw != 0
    return default


def _parse_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        normalized = raw.strip()
        return [normalized] if normalized else []
    if isinstance(raw, list | tuple | set):
        return [str(tag) for tag in raw if str(tag).strip()]
    return []


def _entry_from_payload(entry_id: UUID, payload: dict[str, Any], vector: list[float] | None) -> MemoryEntry:
    level_raw = str(payload.get("security_level", SecurityLevel.INTERNAL.value))
    try:
        level = SecurityLevel(level_raw)
    except ValueError:
        level = SecurityLevel.INTERNAL
    return MemoryEntry(
        id=entry_id,
        agent_id=str(payload.get("agent_id", "")),
        tenant_id=str(payload.get("tenant_id", "default")),
        content=str(payload.get("content", "")),
        embedding=vector,
        tags=_parse_tags(payload.get("tags")),
        security_level=level,
        version=_parse_int(payload.get("version", 1), 1),
        created_at=_parse_dt(payload.get("created_at")),
        accessed_at=_parse_dt(payload["accessed_at"]) if payload.get("accessed_at") else None,
        access_count=_parse_int(payload.get("access_count", 0), 0),
        archived=_parse_bool(payload.get("archived", False), default=False),
    )


def _coerce_vector(raw: Any) -> list[float] | None:
    if isinstance(raw, list) and all(isinstance(item, int | float) for item in raw):
        return [float(item) for item in raw]
    return None


class QdrantStore(MemoryStore):
    """Qdrant implementation with the same contract as PgVectorStore."""

    def __init__(
        self,
        url: str,
        collection_name: str,
        embedding_dimensions: int = 1536,
        time_decay_half_life_hours: float = 168.0,
        client: Any | None = None,
    ) -> None:
        if embedding_dimensions <= 0:
            raise ValueError("embedding_dimensions must be > 0")
        if client is None and AsyncQdrantClient is None:
            raise RuntimeError("QdrantStore requires qdrant-client. Install with `poetry add qdrant-client`.")
        self._client: Any = client or AsyncQdrantClient(url=url)
        self._collection_name = collection_name
        self._embedding_dimensions = embedding_dimensions
        self._time_decay_half_life_hours = time_decay_half_life_hours
        self._collection_ready = False

    async def _ensure_collection(self) -> None:
        if self._collection_ready:
            return
        assert qmodels is not None
        if await self._client.collection_exists(self._collection_name):
            self._collection_ready = True
            return
        await self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=qmodels.VectorParams(
                size=self._embedding_dimensions,
                distance=qmodels.Distance.COSINE,
            ),
        )
        self._collection_ready = True

    @staticmethod
    def _base_filter(agent_id: str, tenant_id: str, tags: list[str] | None, include_archived: bool) -> Any:
        assert qmodels is not None
        must = [
            qmodels.FieldCondition(key="agent_id", match=qmodels.MatchValue(value=agent_id)),
            qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)),
        ]
        if not include_archived:
            must.append(qmodels.FieldCondition(key="archived", match=qmodels.MatchValue(value=False)))
        if tags:
            for tag in tags:
                must.append(qmodels.FieldCondition(key="tags", match=qmodels.MatchValue(value=tag)))
        return qmodels.Filter(must=cast(Any, must))

    async def save(self, entry: MemoryEntry) -> UUID:
        await self._ensure_collection()
        assert qmodels is not None
        payload = {
            "agent_id": entry.agent_id,
            "tenant_id": entry.tenant_id,
            "content": entry.content,
            "tags": entry.tags,
            "security_level": entry.security_level.value,
            "version": entry.version,
            "created_at": entry.created_at.isoformat(),
            "accessed_at": entry.accessed_at.isoformat() if entry.accessed_at else None,
            "access_count": entry.access_count,
            "archived": entry.archived,
        }
        vector = entry.embedding if entry.embedding is not None else [0.0] * self._embedding_dimensions
        await self._client.upsert(
            collection_name=self._collection_name,
            points=[
                qmodels.PointStruct(
                    id=str(entry.id),
                    payload=payload,
                    vector=vector,
                )
            ],
        )
        return entry.id

    async def search(
        self,
        agent_id: str,
        tenant_id: str,
        query_embedding: list[float] | None,
        limit: int = 5,
        tags: list[str] | None = None,
        include_archived: bool = False,
    ) -> list[tuple[MemoryEntry, float]]:
        await self._ensure_collection()
        filt = self._base_filter(agent_id, tenant_id, tags, include_archived)
        now = _now_utc()

        if query_embedding is None:
            records, _ = await self._client.scroll(
                collection_name=self._collection_name,
                scroll_filter=filt,
                limit=max(limit * 4, 50),
                with_payload=True,
                with_vectors=True,
            )
            entries = []
            for rec in records:
                entry = _entry_from_payload(UUID(str(rec.id)), rec.payload or {}, _coerce_vector(rec.vector))
                entries.append(entry)
            entries.sort(key=lambda e: e.created_at, reverse=True)
            return [(e, 1.0) for e in entries[:limit]]

        points = await self._client.search(
            collection_name=self._collection_name,
            query_vector=query_embedding,
            query_filter=filt,
            limit=limit * 5,
            with_payload=True,
            with_vectors=True,
        )
        out: list[tuple[MemoryEntry, float]] = []
        for point in points:
            entry = _entry_from_payload(
                UUID(str(point.id)),
                point.payload or {},
                _coerce_vector(point.vector),
            )
            age_hours = (now - entry.created_at).total_seconds() / 3600.0
            score = float(point.score) * time_decay(age_hours, self._time_decay_half_life_hours)
            out.append((entry, score))
        out.sort(key=lambda item: item[1], reverse=True)
        return out[:limit]

    async def get_recent(
        self,
        agent_id: str,
        tenant_id: str,
        hours: int = 24,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        await self._ensure_collection()
        records, _ = await self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=self._base_filter(agent_id, tenant_id, tags=None, include_archived=False),
            limit=max(limit * 5, 100),
            with_payload=True,
            with_vectors=True,
        )
        cutoff = _now_utc() - timedelta(hours=hours) if hours > 0 else None
        entries: list[MemoryEntry] = []
        for rec in records:
            entry = _entry_from_payload(UUID(str(rec.id)), rec.payload or {}, _coerce_vector(rec.vector))
            if cutoff is not None and entry.created_at < cutoff:
                continue
            if entry.archived:
                continue
            entries.append(entry)
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries[:limit]

    async def archive(self, entry_ids: list[UUID]) -> int:
        await self._ensure_collection()
        if not entry_ids:
            return 0
        assert qmodels is not None
        points = [str(uid) for uid in entry_ids]
        await self._client.set_payload(
            collection_name=self._collection_name,
            payload={"archived": True},
            points=cast(Any, points),
        )
        return len(points)

    async def delete(self, entry_ids: list[UUID]) -> int:
        await self._ensure_collection()
        if not entry_ids:
            return 0
        assert qmodels is not None
        points = [str(uid) for uid in entry_ids]
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=qmodels.PointIdsList(points=cast(Any, points)),
        )
        return len(points)

    async def count(self, agent_id: str, tenant_id: str) -> int:
        await self._ensure_collection()
        result = await self._client.count(
            collection_name=self._collection_name,
            count_filter=self._base_filter(agent_id, tenant_id, tags=None, include_archived=False),
            exact=True,
        )
        return int(result.count)

    async def update_access(self, agent_id: str, tenant_id: str, entry_ids: list[UUID]) -> None:
        await self._ensure_collection()
        if not entry_ids:
            return
        points = [str(uid) for uid in entry_ids]
        records = await self._client.retrieve(
            collection_name=self._collection_name,
            ids=points,
            with_payload=True,
            with_vectors=False,
        )
        for rec in records:
            payload = rec.payload or {}
            if payload.get("agent_id") != agent_id or payload.get("tenant_id") != tenant_id:
                continue
            current = _parse_int(payload.get("access_count", 0), 0)
            await self._client.set_payload(
                collection_name=self._collection_name,
                payload={
                    "accessed_at": _now_utc().isoformat(),
                    "access_count": current + 1,
                },
                points=[str(rec.id)],
            )

    async def list_entries(
        self,
        agent_id: str,
        tenant_id: str,
        order_created_asc: bool,
        limit: int,
        include_archived: bool = False,
    ) -> list[MemoryEntry]:
        await self._ensure_collection()
        records, _ = await self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=self._base_filter(agent_id, tenant_id, tags=None, include_archived=include_archived),
            limit=max(limit, 1),
            with_payload=True,
            with_vectors=True,
        )
        entries = [
            _entry_from_payload(UUID(str(rec.id)), rec.payload or {}, _coerce_vector(rec.vector))
            for rec in records
        ]
        entries.sort(key=lambda e: e.created_at, reverse=not order_created_asc)
        return entries[:limit]

    async def get_expired_entry_ids(
        self,
        agent_id: str,
        tenant_id: str,
        before: datetime,
        max_access_count: int = 0,
    ) -> list[UUID]:
        entries = await self.list_entries(
            agent_id=agent_id,
            tenant_id=tenant_id,
            order_created_asc=True,
            limit=100000,
            include_archived=False,
        )
        return [
            entry.id
            for entry in entries
            if entry.created_at < before and entry.access_count <= max_access_count and not entry.archived
        ]
