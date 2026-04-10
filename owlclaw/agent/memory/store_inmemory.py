"""In-memory MemoryStore implementation — dict storage + brute-force cosine similarity (mock_mode / tests)."""

from __future__ import annotations

import asyncio
import heapq
import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID

from owlclaw.agent.memory.decay import time_decay
from owlclaw.agent.memory.models import MemoryEntry
from owlclaw.agent.memory.store import MemoryStore


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [0, 1] for non-negative normalized vectors; 0 if any norm is 0."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na <= 0 or nb <= 0:
        return 0.0
    sim = dot / (na * nb)
    return max(0.0, min(1.0, sim))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryStore(MemoryStore):
    """In-memory store: dict + brute-force cosine search. For mock_mode and tests."""

    def __init__(self, time_decay_half_life_hours: float = 168.0, max_entries: int = 10_000) -> None:
        self._store: dict[UUID, MemoryEntry] = {}
        self._embedding_norms: dict[UUID, float] = {}
        self._search_cache: dict[tuple[str, str, tuple[float, ...], int, tuple[str, ...], bool], list[tuple[UUID, float]]] = {}
        self._time_decay_half_life_hours = time_decay_half_life_hours
        self._max_entries = max(1, int(max_entries))
        self._lock = asyncio.Lock()

    def _copy(self, entry: MemoryEntry) -> MemoryEntry:
        return deepcopy(entry)

    def _touch_sort_key(self, entry: MemoryEntry) -> datetime:
        return entry.accessed_at or entry.created_at

    def _evict_if_needed(self) -> None:
        overflow = len(self._store) - self._max_entries
        if overflow <= 0:
            return
        candidates = sorted(self._store.values(), key=self._touch_sort_key)
        for entry in candidates[:overflow]:
            self._store.pop(entry.id, None)
            self._embedding_norms.pop(entry.id, None)

    async def save(self, entry: MemoryEntry) -> UUID:
        async with self._lock:
            copy = self._copy(entry)
            self._store[copy.id] = copy
            if copy.embedding:
                self._embedding_norms[copy.id] = math.sqrt(sum(x * x for x in copy.embedding))
            else:
                self._embedding_norms[copy.id] = 0.0
            self._evict_if_needed()
            self._search_cache.clear()
            return copy.id

    async def search(
        self,
        agent_id: str,
        tenant_id: str,
        query_embedding: list[float] | None,
        limit: int = 5,
        tags: list[str] | None = None,
        include_archived: bool = False,
    ) -> list[tuple[MemoryEntry, float]]:
        async with self._lock:
            candidates = [
                e for e in self._store.values()
                if e.agent_id == agent_id and e.tenant_id == tenant_id
                and (include_archived or not e.archived)
            ]
            if tags:
                for t in tags:
                    candidates = [e for e in candidates if t in (e.tags or [])]
            if query_embedding is None:
                candidates.sort(key=lambda e: e.created_at, reverse=True)
                return [(self._copy(e), 1.0) for e in candidates[:limit]]
            cache_key = (
                agent_id,
                tenant_id,
                tuple(round(v, 6) for v in query_embedding),
                limit,
                tuple(sorted(tags or [])),
                include_archived,
            )
            cached = self._search_cache.get(cache_key)
            if cached is not None:
                out: list[tuple[MemoryEntry, float]] = []
                for entry_id, score in cached:
                    entry = self._store.get(entry_id)
                    if entry is None:
                        continue
                    out.append((self._copy(entry), score))
                return out
            now = _now_utc()
            query_norm = math.sqrt(sum(x * x for x in query_embedding))
            if query_norm <= 0:
                return []
            scored: list[tuple[float, MemoryEntry]] = []
            for e in candidates:
                if not e.embedding:
                    continue
                emb_norm = self._embedding_norms.get(e.id, 0.0)
                if emb_norm <= 0:
                    continue
                dot = sum(x * y for x, y in zip(query_embedding, e.embedding, strict=True))
                sim = max(0.0, min(1.0, dot / (query_norm * emb_norm)))
                created = e.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_hours = (now - created).total_seconds() / 3600.0
                final = sim * time_decay(age_hours, self._time_decay_half_life_hours)
                scored.append((final, e))
            top = heapq.nlargest(limit, scored, key=lambda item: item[0])
            self._search_cache[cache_key] = [(entry.id, score) for score, entry in top]
            return [(self._copy(entry), score) for score, entry in top]

    async def get_recent(
        self,
        agent_id: str,
        tenant_id: str,
        hours: int = 24,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        async with self._lock:
            cutoff: datetime | None = None
            if hours > 0:
                cutoff = _now_utc() - timedelta(hours=hours)
            candidates = []
            for e in self._store.values():
                if e.agent_id != agent_id or e.tenant_id != tenant_id or e.archived:
                    continue
                created = e.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if cutoff is None or created >= cutoff:
                    candidates.append(e)
            candidates.sort(key=lambda e: e.created_at, reverse=True)
            return [self._copy(e) for e in candidates[:limit]]

    async def archive(self, entry_ids: list[UUID]) -> int:
        async with self._lock:
            n = 0
            for uid in entry_ids:
                if uid in self._store:
                    self._store[uid].archived = True
                    n += 1
            if n:
                self._search_cache.clear()
            return n

    async def delete(self, entry_ids: list[UUID]) -> int:
        async with self._lock:
            n = 0
            for uid in entry_ids:
                if uid in self._store:
                    del self._store[uid]
                    self._embedding_norms.pop(uid, None)
                    n += 1
            if n:
                self._search_cache.clear()
            return n

    async def count(self, agent_id: str, tenant_id: str) -> int:
        async with self._lock:
            return sum(
                1 for e in self._store.values()
                if e.agent_id == agent_id and e.tenant_id == tenant_id and not e.archived
            )

    async def update_access(
        self, agent_id: str, tenant_id: str, entry_ids: list[UUID]
    ) -> None:
        async with self._lock:
            now = _now_utc()
            for uid in entry_ids:
                e = self._store.get(uid)
                if e is None or e.agent_id != agent_id or e.tenant_id != tenant_id:
                    continue
                e.accessed_at = now
                e.access_count += 1

    async def list_entries(
        self,
        agent_id: str,
        tenant_id: str,
        order_created_asc: bool,
        limit: int,
        include_archived: bool = False,
    ) -> list[MemoryEntry]:
        async with self._lock:
            candidates = [
                e for e in self._store.values()
                if e.agent_id == agent_id and e.tenant_id == tenant_id
                and (include_archived or not e.archived)
            ]
            candidates.sort(key=lambda e: e.created_at, reverse=not order_created_asc)
            return [self._copy(e) for e in candidates[:limit]]

    async def get_expired_entry_ids(
        self,
        agent_id: str,
        tenant_id: str,
        before: datetime,
        max_access_count: int = 0,
    ) -> list[UUID]:
        async with self._lock:
            out = []
            for e in self._store.values():
                if e.agent_id != agent_id or e.tenant_id != tenant_id or e.archived:
                    continue
                created = e.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < before and e.access_count <= max_access_count:
                    out.append(e.id)
            return out
