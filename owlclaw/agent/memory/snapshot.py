"""Snapshot builder — assemble LTM snapshot for Run start (semantic + recent + pinned)."""

from __future__ import annotations

from uuid import UUID

from owlclaw.agent.memory.embedder import EmbeddingProvider
from owlclaw.agent.memory.models import MemoryEntry, MemorySnapshot
from owlclaw.agent.memory.store import MemoryStore

_CHARS_PER_TOKEN = 4


def _count_tokens(text: str) -> int:
    """Token count via tiktoken if available, else ~4 chars per token."""
    if not text:
        return 0
    try:
        import tiktoken  # noqa: PLC0415
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


class SnapshotBuilder:
    """Build LTM snapshot at Run start: semantic search + recent + pinned, dedup and trim."""

    def __init__(self, store: MemoryStore, embedder: EmbeddingProvider) -> None:
        self._store = store
        self._embedder = embedder

    async def build(
        self,
        agent_id: str,
        tenant_id: str,
        trigger_event: str,
        focus: str | None,
        max_tokens: int = 500,
        semantic_limit: int = 3,
        recent_hours: int = 24,
        recent_limit: int = 5,
        pinned_limit: int = 10,
    ) -> MemorySnapshot:
        """Assemble snapshot: top-K semantic + recent window + pinned; dedup and token trim."""
        # 1. Semantic: top-K by trigger_event + optional focus context
        query_text = trigger_event.strip()
        if focus:
            query_text = f"{query_text}\nfocus: {focus.strip()}"
        query_emb = await self._embedder.embed(query_text)
        semantic_hits = await self._store.search(
            agent_id, tenant_id, query_emb, limit=semantic_limit
        )
        # 2. Recent time window
        recent = await self._store.get_recent(
            agent_id, tenant_id, hours=recent_hours, limit=recent_limit
        )
        # 3. Pinned (tag-only search)
        pinned = await self._store.search(
            agent_id, tenant_id, None, limit=pinned_limit, tags=["pinned"]
        )
        return self._assemble(semantic_hits, recent, pinned, max_tokens)

    def _assemble(
        self,
        semantic_hits: list[tuple[MemoryEntry, float]],
        recent: list[MemoryEntry],
        pinned: list[tuple[MemoryEntry, float]],
        max_tokens: int,
    ) -> MemorySnapshot:
        """Dedup by entry id (order: semantic, recent, pinned), then build prompt and trim to max_tokens."""
        seen: set[UUID] = set()
        ordered_entries: list[MemoryEntry] = []
        for entry, _ in semantic_hits:
            if entry.id not in seen:
                seen.add(entry.id)
                ordered_entries.append(entry)
        for entry in recent:
            if entry.id not in seen:
                seen.add(entry.id)
                ordered_entries.append(entry)
        for entry, _ in pinned:
            if entry.id not in seen:
                seen.add(entry.id)
                ordered_entries.append(entry)

        lines: list[str] = []
        entry_ids: list[UUID] = []
        used_tokens = _count_tokens("## Long-term memory\n\n")
        header = "## Long-term memory\n\n"
        for entry in ordered_entries:
            line = f"- {entry.content}\n"
            need = _count_tokens(line)
            if used_tokens + need > max_tokens:
                break
            lines.append(line)
            entry_ids.append(entry.id)
            used_tokens += need

        prompt_fragment = header + "".join(lines) if lines else header + "(no memories)\n"
        return MemorySnapshot(prompt_fragment=prompt_fragment.strip(), entry_ids=entry_ids)
