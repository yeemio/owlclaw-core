"""Memory backend migration helpers (pgvector <-> qdrant)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from owlclaw.agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)


def _normalize_scope(agent_id: str, tenant_id: str) -> tuple[str, str]:
    normalized_agent = agent_id.strip()
    if not normalized_agent:
        raise ValueError("agent_id must not be empty")
    normalized_tenant = tenant_id.strip()
    if not normalized_tenant:
        raise ValueError("tenant_id must not be empty")
    return normalized_agent, normalized_tenant


@dataclass
class MemoryMigrationResult:
    """Migration counters for one agent/tenant scope."""

    moved: int = 0
    failed: int = 0


async def migrate_store_data(
    source: MemoryStore,
    target: MemoryStore,
    agent_id: str,
    tenant_id: str,
    batch_size: int = 200,
    include_archived: bool = True,
) -> MemoryMigrationResult:
    """Copy entries from source store to target store in created_at order."""
    normalized_agent, normalized_tenant = _normalize_scope(agent_id, tenant_id)
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")

    result = MemoryMigrationResult()
    entries = await source.list_entries(
        agent_id=normalized_agent,
        tenant_id=normalized_tenant,
        order_created_asc=True,
        limit=1_000_000,
        include_archived=include_archived,
    )
    for start in range(0, len(entries), batch_size):
        chunk = entries[start : start + batch_size]
        for entry in chunk:
            try:
                await target.save(entry)
                result.moved += 1
            except Exception:
                result.failed += 1
                logger.warning(
                    "memory migration failed for entry_id=%s agent_id=%s tenant_id=%s",
                    entry.id,
                    normalized_agent,
                    normalized_tenant,
                )
    return result
