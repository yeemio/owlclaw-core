"""Memory CLI commands: list, prune, reset, stats."""

import asyncio
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import typer

from owlclaw.agent.memory.migration import migrate_store_data
from owlclaw.agent.memory.models import MemoryConfig, MemoryEntry
from owlclaw.agent.memory.store import MemoryStore
from owlclaw.agent.memory.store_inmemory import InMemoryStore
from owlclaw.agent.memory.store_pgvector import PgVectorStore
from owlclaw.agent.memory.store_qdrant import QdrantStore
from owlclaw.db import create_engine, create_session_factory
from owlclaw.db.exceptions import ConfigurationError

memory_app = typer.Typer(help="Agent memory operations (list, prune, reset, stats).")


def _normalize_scope(agent: str, tenant: str) -> tuple[str, str]:
    normalized_agent = agent.strip()
    if not normalized_agent:
        raise typer.BadParameter("agent must not be empty.")
    normalized_tenant = tenant.strip()
    if not normalized_tenant:
        raise typer.BadParameter("tenant must not be empty.")
    return normalized_agent, normalized_tenant


def _normalize_backend(backend: str) -> str:
    normalized = backend.strip().lower()
    if normalized not in {"pgvector", "inmemory", "qdrant"}:
        raise typer.BadParameter("backend must be one of: pgvector, inmemory, qdrant.")
    return normalized


def _parse_tags(tags: str) -> list[str]:
    if not tags.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags.split(","):
        t = raw.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _parse_before(before: str) -> datetime | None:
    value = before.strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("before must be ISO datetime, e.g. 2026-02-23T10:00:00+00:00") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _security_marker(entry: MemoryEntry) -> str:
    if entry.security_level.value in {"confidential", "restricted"}:
        return "[CONFIDENTIAL] "
    return ""


def _matches_tags(entry: MemoryEntry, wanted: list[str]) -> bool:
    if not wanted:
        return True
    tags = set(entry.tags or [])
    return all(t in tags for t in wanted)


def _create_store(backend: str) -> MemoryStore:
    normalized_backend = _normalize_backend(backend)
    config = MemoryConfig(vector_backend=normalized_backend)
    if normalized_backend == "inmemory":
        return InMemoryStore(time_decay_half_life_hours=config.time_decay_half_life_hours)
    if normalized_backend == "pgvector":
        db_url = os.environ.get("OWLCLAW_DATABASE_URL", "").strip()
        if not db_url:
            raise ConfigurationError("OWLCLAW_DATABASE_URL is required when memory backend is pgvector.")
        engine = create_engine(db_url)
        session_factory = create_session_factory(engine)
        return PgVectorStore(
            session_factory=session_factory,
            embedding_dimensions=config.embedding_dimensions,
            time_decay_half_life_hours=config.time_decay_half_life_hours,
        )
    if normalized_backend == "qdrant":
        qdrant_url = os.environ.get("OWLCLAW_QDRANT_URL", config.qdrant_url).strip()
        qdrant_collection = os.environ.get("OWLCLAW_QDRANT_COLLECTION", config.qdrant_collection_name).strip()
        if not qdrant_url:
            raise ConfigurationError("OWLCLAW_QDRANT_URL must not be empty when memory backend is qdrant.")
        if not qdrant_collection:
            raise ConfigurationError("OWLCLAW_QDRANT_COLLECTION must not be empty when memory backend is qdrant.")
        return QdrantStore(
            url=qdrant_url,
            collection_name=qdrant_collection,
            embedding_dimensions=config.embedding_dimensions,
            time_decay_half_life_hours=config.time_decay_half_life_hours,
        )
    raise ConfigurationError(f"Unsupported memory backend: {normalized_backend}")


async def _list_entries_impl(
    store: MemoryStore,
    agent: str,
    tenant: str,
    tags: list[str],
    page: int,
    page_size: int,
    include_archived: bool,
) -> list[MemoryEntry]:
    fetch_limit = page * page_size
    entries = await store.list_entries(
        agent_id=agent,
        tenant_id=tenant,
        order_created_asc=False,
        limit=fetch_limit,
        include_archived=include_archived,
    )
    filtered = [e for e in entries if _matches_tags(e, tags)]
    start = (page - 1) * page_size
    return filtered[start : start + page_size]


async def _prune_impl(
    store: MemoryStore,
    agent: str,
    tenant: str,
    before: datetime | None,
    tags: list[str],
) -> int:
    entries = await store.list_entries(
        agent_id=agent,
        tenant_id=tenant,
        order_created_asc=False,
        limit=100000,
        include_archived=False,
    )
    ids = []
    for entry in entries:
        if before is not None:
            created = entry.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created >= before:
                continue
        if not _matches_tags(entry, tags):
            continue
        ids.append(entry.id)
    return await store.delete(ids)


async def _reset_impl(store: MemoryStore, agent: str, tenant: str) -> int:
    entries = await store.list_entries(
        agent_id=agent,
        tenant_id=tenant,
        order_created_asc=False,
        limit=100000,
        include_archived=True,
    )
    return await store.delete([e.id for e in entries])


async def _stats_impl(store: MemoryStore, agent: str, tenant: str) -> dict[str, Any]:
    entries = await store.list_entries(
        agent_id=agent,
        tenant_id=tenant,
        order_created_asc=False,
        limit=100000,
        include_archived=True,
    )
    active_count = await store.count(agent, tenant)
    archived_count = sum(1 for e in entries if e.archived)
    storage_size_bytes = sum(len(e.content.encode("utf-8")) for e in entries)
    tag_counter: Counter[str] = Counter()
    for entry in entries:
        for tag in entry.tags or []:
            tag_counter[tag] += 1
    return {
        "total_entries": active_count,
        "archived_entries": archived_count,
        "storage_size_bytes": storage_size_bytes,
        "tag_distribution": dict(tag_counter),
    }


async def _migrate_impl(
    source: MemoryStore,
    target: MemoryStore,
    agent: str,
    tenant: str,
    batch_size: int,
    include_archived: bool,
) -> tuple[int, int]:
    result = await migrate_store_data(
        source=source,
        target=target,
        agent_id=agent,
        tenant_id=tenant,
        batch_size=batch_size,
        include_archived=include_archived,
    )
    return result.moved, result.failed


@memory_app.command("list")
def list_command(
    agent: str = typer.Option(..., "--agent", help="Agent id."),
    tenant: str = typer.Option("default", "--tenant", help="Tenant id."),
    tags: str = typer.Option("", "--tags", help="Comma separated tags filter."),
    page: int = typer.Option(1, "--page", min=1, help="Page number (1-based)."),
    page_size: int = typer.Option(20, "--page-size", min=1, max=200, help="Page size."),
    include_archived: bool = typer.Option(False, "--include-archived", help="Include archived entries."),
    backend: str = typer.Option("pgvector", "--backend", help="Memory backend: pgvector|inmemory|qdrant."),
) -> None:
    normalized_agent, normalized_tenant = _normalize_scope(agent, tenant)
    store = _create_store(_normalize_backend(backend))
    parsed_tags = _parse_tags(tags)
    rows = asyncio.run(
        _list_entries_impl(
            store=store,
            agent=normalized_agent,
            tenant=normalized_tenant,
            tags=parsed_tags,
            page=page,
            page_size=page_size,
            include_archived=include_archived,
        )
    )
    if not rows:
        typer.echo("No memory entries found.")
        return
    for entry in rows:
        created = entry.created_at.isoformat()
        preview = entry.content.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        preview = _security_marker(entry) + preview
        typer.echo(
            f"{entry.id} | created_at={created} | tags={','.join(entry.tags)} | "
            f"access_count={entry.access_count} | archived={entry.archived} | {preview}"
        )


@memory_app.command("prune")
def prune_command(
    agent: str = typer.Option(..., "--agent", help="Agent id."),
    tenant: str = typer.Option("default", "--tenant", help="Tenant id."),
    before: str = typer.Option("", "--before", help="Delete entries before this ISO datetime."),
    tags: str = typer.Option("", "--tags", help="Comma separated tags filter."),
    backend: str = typer.Option("pgvector", "--backend", help="Memory backend: pgvector|inmemory|qdrant."),
) -> None:
    normalized_agent, normalized_tenant = _normalize_scope(agent, tenant)
    store = _create_store(_normalize_backend(backend))
    deleted = asyncio.run(
        _prune_impl(
            store=store,
            agent=normalized_agent,
            tenant=normalized_tenant,
            before=_parse_before(before),
            tags=_parse_tags(tags),
        )
    )
    typer.echo(f"Deleted {deleted} entries.")


@memory_app.command("reset")
def reset_command(
    agent: str = typer.Option(..., "--agent", help="Agent id."),
    tenant: str = typer.Option("default", "--tenant", help="Tenant id."),
    confirm: bool = typer.Option(False, "--confirm", help="Required confirmation flag."),
    backend: str = typer.Option("pgvector", "--backend", help="Memory backend: pgvector|inmemory|qdrant."),
) -> None:
    if not confirm:
        raise typer.BadParameter("--confirm is required for reset.")
    normalized_agent, normalized_tenant = _normalize_scope(agent, tenant)
    store = _create_store(_normalize_backend(backend))
    deleted = asyncio.run(_reset_impl(store=store, agent=normalized_agent, tenant=normalized_tenant))
    typer.echo(f"Reset completed. Deleted {deleted} entries.")


@memory_app.command("stats")
def stats_command(
    agent: str = typer.Option(..., "--agent", help="Agent id."),
    tenant: str = typer.Option("default", "--tenant", help="Tenant id."),
    backend: str = typer.Option("pgvector", "--backend", help="Memory backend: pgvector|inmemory|qdrant."),
) -> None:
    normalized_agent, normalized_tenant = _normalize_scope(agent, tenant)
    store = _create_store(_normalize_backend(backend))
    stats = asyncio.run(_stats_impl(store=store, agent=normalized_agent, tenant=normalized_tenant))
    typer.echo(f"total_entries: {stats['total_entries']}")
    typer.echo(f"archived_entries: {stats['archived_entries']}")
    typer.echo(f"storage_size_bytes: {stats['storage_size_bytes']}")
    tag_distribution = stats["tag_distribution"]
    if not tag_distribution:
        typer.echo("tag_distribution: {}")
        return
    typer.echo("tag_distribution:")
    for tag, count in sorted(tag_distribution.items()):
        typer.echo(f"  {tag}: {count}")


@memory_app.command("migrate-backend")
def migrate_backend_command(
    agent: str = typer.Option(..., "--agent", help="Agent id."),
    tenant: str = typer.Option("default", "--tenant", help="Tenant id."),
    source_backend: str = typer.Option(..., "--source-backend", help="Source backend: pgvector|qdrant|inmemory."),
    target_backend: str = typer.Option(..., "--target-backend", help="Target backend: pgvector|qdrant|inmemory."),
    batch_size: int = typer.Option(200, "--batch-size", min=1, max=5000, help="Migration batch size."),
    include_archived: bool = typer.Option(True, "--include-archived", help="Include archived entries (use --no-include-archived to exclude)."),
) -> None:
    normalized_agent, normalized_tenant = _normalize_scope(agent, tenant)
    normalized_source = _normalize_backend(source_backend)
    normalized_target = _normalize_backend(target_backend)
    if normalized_source == normalized_target:
        raise typer.BadParameter("source and target backend must be different.")
    source = _create_store(normalized_source)
    target = _create_store(normalized_target)
    moved, failed = asyncio.run(
        _migrate_impl(
            source=source,
            target=target,
            agent=normalized_agent,
            tenant=normalized_tenant,
            batch_size=batch_size,
            include_archived=include_archived,
        )
    )
    typer.echo(f"Migration completed. moved={moved}, failed={failed}")
