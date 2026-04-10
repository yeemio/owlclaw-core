"""Memory storage abstraction — MemoryStore ABC and implementations (PgVector, InMemory, Qdrant)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from owlclaw.agent.memory.models import MemoryEntry


class MemoryStore(ABC):
    """Abstract base for LTM storage (vector + metadata)."""

    @abstractmethod
    async def save(self, entry: MemoryEntry) -> UUID:
        """Persist one memory entry; return its id."""
        ...

    @abstractmethod
    async def search(
        self,
        agent_id: str,
        tenant_id: str,
        query_embedding: list[float] | None,
        limit: int = 5,
        tags: list[str] | None = None,
        include_archived: bool = False,
    ) -> list[tuple[MemoryEntry, float]]:
        """Vector similarity search; returns (entry, score) list. query_embedding=None for tag-only (e.g. pinned)."""
        ...

    @abstractmethod
    async def get_recent(
        self,
        agent_id: str,
        tenant_id: str,
        hours: int = 24,
        limit: int = 5,
    ) -> list[MemoryEntry]:
        """Return most recent entries by created_at in time window."""
        ...

    @abstractmethod
    async def archive(self, entry_ids: list[UUID]) -> int:
        """Mark entries as archived; return count updated."""
        ...

    @abstractmethod
    async def delete(self, entry_ids: list[UUID]) -> int:
        """Permanently delete entries; return count deleted."""
        ...

    @abstractmethod
    async def count(self, agent_id: str, tenant_id: str) -> int:
        """Return total entry count for agent/tenant (excluding archived by default if impl supports)."""
        ...

    @abstractmethod
    async def update_access(
        self, agent_id: str, tenant_id: str, entry_ids: list[UUID]
    ) -> None:
        """Update accessed_at and increment access_count for given entries (e.g. after recall)."""
        ...

    @abstractmethod
    async def list_entries(
        self,
        agent_id: str,
        tenant_id: str,
        order_created_asc: bool,
        limit: int,
        include_archived: bool = False,
    ) -> list[MemoryEntry]:
        """List entries by created_at order; for lifecycle (archive excess)."""
        ...

    @abstractmethod
    async def get_expired_entry_ids(
        self,
        agent_id: str,
        tenant_id: str,
        before: datetime,
        max_access_count: int = 0,
    ) -> list[UUID]:
        """Return entry ids with created_at < before and access_count <= max_access_count (non-archived)."""
        ...
