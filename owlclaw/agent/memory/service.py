"""Memory façade — MemoryService (remember/recall, STM, snapshot)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from owlclaw.agent.memory.embedder import EmbeddingProvider
from owlclaw.agent.memory.models import (
    CompactionResult,
    MemoryConfig,
    MemoryEntry,
    MemorySnapshot,
    RecallResult,
    SecurityLevel,
)
from owlclaw.agent.memory.security import MemorySecurityFilter, SecurityClassifier
from owlclaw.agent.memory.snapshot import SnapshotBuilder
from owlclaw.agent.memory.stm import STMManager
from owlclaw.agent.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class MemoryService:
    """Single entry point for memory: Agent Tools and Runtime use this."""

    def __init__(
        self,
        store: MemoryStore,
        embedder: EmbeddingProvider,
        config: MemoryConfig,
        ledger: Any = None,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._config = config
        self._ledger = ledger
        self._snapshot_builder = SnapshotBuilder(store, embedder)
        self._classifier = SecurityClassifier()
        self._security_filter = MemorySecurityFilter()
        self._fallback_embedder: EmbeddingProvider | None = None

    def _get_fallback_embedder(self) -> EmbeddingProvider:
        if self._fallback_embedder is not None:
            return self._fallback_embedder
        from owlclaw.agent.memory.embedder_tfidf import TFIDFEmbedder

        self._fallback_embedder = TFIDFEmbedder(dimensions=self._config.tfidf_dimensions)
        return self._fallback_embedder

    async def _embed_with_fallback(self, text: str) -> list[float] | None:
        try:
            return await self._embedder.embed(text)
        except Exception:
            if not self._config.enable_tfidf_fallback:
                raise
            await self._record_degradation("embedding_fallback_tfidf", {"text_size": len(text)})
            logger.warning("memory embedding failed; using TF-IDF fallback")
            return await self._get_fallback_embedder().embed(text)

    @staticmethod
    def _keyword_score(query: str, content: str) -> float:
        q_words = {x for x in query.lower().split() if x}
        if not q_words:
            return 0.0
        c_words = {x for x in content.lower().split() if x}
        return len(q_words & c_words) / len(q_words)

    async def _keyword_recall_fallback(
        self,
        agent_id: str,
        tenant_id: str,
        query: str,
        limit: int,
        tags: list[str],
        channel: str,
    ) -> list[RecallResult]:
        entries = await self._store.list_entries(
            agent_id=agent_id,
            tenant_id=tenant_id,
            order_created_asc=False,
            limit=5000,
            include_archived=False,
        )
        scored = []
        tag_set = set(tags)
        for entry in entries:
            if tag_set and not tag_set.issubset(set(entry.tags or [])):
                continue
            score = self._keyword_score(query, entry.content)
            if score <= 0:
                continue
            scored.append((entry, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        chosen = scored[:limit]
        if chosen:
            await self._store.update_access(agent_id, tenant_id, [entry.id for entry, _ in chosen])
        return [
            RecallResult(entry=self._security_filter.for_channel(entry, channel=channel), score=score)
            for entry, score in chosen
        ]

    async def _append_file_fallback(
        self,
        agent_id: str,
        tenant_id: str,
        content: str,
        tags: list[str],
        security_level: SecurityLevel,
    ) -> UUID:
        path = Path(self._config.file_fallback_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        memory_id = uuid4()
        sanitized_content = content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
        safe_tags = [tag.replace(",", "_").strip() for tag in tags if tag.strip()]
        line = (
            f"- id: {memory_id}\n"
            f"  tenant_id: {tenant_id}\n"
            f"  agent_id: {agent_id}\n"
            f"  security_level: {security_level.value}\n"
            f"  tags: [{', '.join(safe_tags)}]\n"
            f"  content: {sanitized_content}\n"
        )
        path.write_text(path.read_text(encoding="utf-8") + line if path.exists() else line, encoding="utf-8")
        await self._record_degradation("store_fallback_file", {"path": str(path)})
        return memory_id

    async def _record_degradation(self, event: str, payload: dict[str, Any]) -> None:
        if self._ledger is None or not hasattr(self._ledger, "record_execution"):
            return
        try:
            await self._ledger.record_execution(  # type: ignore[misc]
                tenant_id=payload.get("tenant_id", "default"),
                agent_id=payload.get("agent_id", "memory"),
                run_id="memory-degradation",
                capability_name=f"memory.{event}",
                task_type="memory",
                input_params=payload,
                output_result={"event": event},
                decision_reasoning="fallback activated",
                execution_time_ms=0,
                llm_model="",
                llm_tokens_input=0,
                llm_tokens_output=0,
                estimated_cost=0,
                status="success",
                error_message=None,
            )
        except Exception:
            logger.exception("failed to record degradation event: %s", event)

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        if not tags:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for raw in tags:
            normalized = raw.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    @staticmethod
    def _normalize_scope(agent_id: str, tenant_id: str) -> tuple[str, str]:
        normalized_agent = agent_id.strip()
        if not normalized_agent:
            raise ValueError("agent_id must not be empty")
        normalized_tenant = tenant_id.strip()
        if not normalized_tenant:
            raise ValueError("tenant_id must not be empty")
        return normalized_agent, normalized_tenant

    async def remember(
        self,
        agent_id: str,
        tenant_id: str,
        content: str,
        tags: list[str] | None = None,
        sensitivity: str | None = None,
    ) -> UUID:
        """Store one memory (embed + save). Optionally Ledger can be wired later."""
        normalized_agent, normalized_tenant = self._normalize_scope(agent_id, tenant_id)
        normalized = content.strip()
        if not normalized:
            raise ValueError("content must not be empty")
        if len(normalized) > 2000:
            raise ValueError("content length must be <= 2000")
        embedding = await self._embed_with_fallback(normalized)
        security_level = self._resolve_security_level(normalized, sensitivity)
        entry = MemoryEntry(
            agent_id=normalized_agent,
            tenant_id=normalized_tenant,
            content=normalized,
            embedding=embedding,
            tags=self._normalize_tags(tags),
            security_level=security_level,
        )
        try:
            return await self._store.save(entry)
        except Exception:
            if not self._config.enable_file_fallback:
                raise
            logger.warning("memory store save failed; writing fallback file")
            return await self._append_file_fallback(
                agent_id=normalized_agent,
                tenant_id=normalized_tenant,
                content=normalized,
                tags=entry.tags,
                security_level=security_level,
            )

    def _resolve_security_level(self, content: str, sensitivity: str | None) -> SecurityLevel:
        """Resolve memory security level from explicit sensitivity or classifier."""
        if sensitivity is None:
            return self._classifier.classify(content)
        normalized = sensitivity.strip().lower()
        mapping = {
            "public": SecurityLevel.PUBLIC,
            "internal": SecurityLevel.INTERNAL,
            "confidential": SecurityLevel.CONFIDENTIAL,
        }
        if normalized not in mapping:
            raise ValueError("sensitivity must be one of: public, internal, confidential")
        return mapping[normalized]

    async def recall(
        self,
        agent_id: str,
        tenant_id: str,
        query: str,
        limit: int = 5,
        tags: list[str] | None = None,
        channel: str = "internal",
    ) -> list[RecallResult]:
        """Search memories by query (embed + search + update access). Returns list of RecallResult."""
        normalized_agent, normalized_tenant = self._normalize_scope(agent_id, tenant_id)
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        safe_limit = max(1, min(limit, 20))
        query_embedding = await self._embed_with_fallback(normalized_query)
        normalized_tags = self._normalize_tags(tags)
        try:
            pairs = await self._store.search(
                normalized_agent,
                normalized_tenant,
                query_embedding,
                limit=safe_limit,
                tags=normalized_tags,
            )
        except Exception:
            if not self._config.enable_keyword_fallback:
                raise
            await self._record_degradation(
                "vector_search_keyword_fallback",
                {"agent_id": normalized_agent, "tenant_id": normalized_tenant},
            )
            logger.warning("memory vector search failed; using keyword fallback")
            return await self._keyword_recall_fallback(
                agent_id=normalized_agent,
                tenant_id=normalized_tenant,
                query=normalized_query,
                limit=safe_limit,
                tags=normalized_tags,
                channel=channel,
            )
        if not pairs:
            return []
        entry_ids = [entry.id for entry, _ in pairs]
        await self._store.update_access(normalized_agent, normalized_tenant, entry_ids)
        out: list[RecallResult] = []
        for entry, score in pairs:
            filtered = self._security_filter.for_channel(entry, channel=channel)
            out.append(RecallResult(entry=filtered, score=score))
        return out

    @staticmethod
    def _summary_fallback(entries: list[MemoryEntry], max_chars: int = 600) -> str:
        chunks = [entry.content.strip() for entry in entries if entry.content.strip()]
        joined = " | ".join(chunks)
        if len(joined) <= max_chars:
            return joined
        return joined[: max_chars - 3] + "..."

    async def compact(
        self,
        agent_id: str,
        tenant_id: str,
    ) -> CompactionResult:
        """Merge large same-tag memory groups into summary entries."""
        normalized_agent, normalized_tenant = self._normalize_scope(agent_id, tenant_id)
        threshold = self._config.compaction_threshold
        entries = await self._store.list_entries(
            agent_id=normalized_agent,
            tenant_id=normalized_tenant,
            order_created_asc=True,
            limit=100000,
            include_archived=False,
        )
        groups: dict[str, list[MemoryEntry]] = {}
        for entry in entries:
            for tag in entry.tags or []:
                groups.setdefault(tag, []).append(entry)

        result = CompactionResult()
        for tag, group in groups.items():
            if len(group) < threshold:
                continue
            summary_text = self._summary_fallback(group)
            summary_embedding = await self._embed_with_fallback(summary_text)
            summary = MemoryEntry(
                agent_id=normalized_agent,
                tenant_id=normalized_tenant,
                content=f"[compaction:{tag}] {summary_text}",
                embedding=summary_embedding,
                tags=[tag, "compacted"],
                security_level=SecurityLevel.INTERNAL,
            )
            await self._store.save(summary)
            archived = await self._store.archive([entry.id for entry in group])
            result.merged_groups += 1
            result.archived_entries += archived
            result.created_summaries += 1
        return result

    def create_stm(self, max_tokens: int = 2000) -> STMManager:
        """Create per-Run STM manager."""
        return STMManager(max_tokens=max_tokens)

    async def build_snapshot(
        self,
        agent_id: str,
        tenant_id: str,
        trigger_event: str,
        focus: str | None = None,
    ) -> MemorySnapshot:
        """Build LTM snapshot for Run start."""
        normalized_agent, normalized_tenant = self._normalize_scope(agent_id, tenant_id)
        return await self._snapshot_builder.build(
            normalized_agent,
            normalized_tenant,
            trigger_event,
            focus,
            max_tokens=self._config.snapshot_max_tokens,
        )

    @classmethod
    def from_config(
        cls,
        config: MemoryConfig,
        session_factory: Any = None,
    ) -> MemoryService:
        """Build MemoryService from config: choose store and embedder by vector_backend.

        - vector_backend \"inmemory\": InMemoryStore + RandomEmbedder (mock/tests).
        - vector_backend \"pgvector\": PgVectorStore + LiteLLMEmbedder; session_factory required.
        """
        from owlclaw.agent.memory.embedder_litellm import LiteLLMEmbedder
        from owlclaw.agent.memory.embedder_random import RandomEmbedder
        from owlclaw.agent.memory.store_inmemory import InMemoryStore
        from owlclaw.agent.memory.store_pgvector import PgVectorStore
        from owlclaw.agent.memory.store_qdrant import QdrantStore

        embedder: EmbeddingProvider
        if config.vector_backend == "inmemory":
            store: MemoryStore = InMemoryStore(
                time_decay_half_life_hours=config.time_decay_half_life_hours,
            )
            embedder = RandomEmbedder(dimensions=config.embedding_dimensions)
        elif config.vector_backend == "pgvector":
            if session_factory is None:
                raise ValueError("session_factory is required when vector_backend is pgvector")
            store = PgVectorStore(
                session_factory=session_factory,
                embedding_dimensions=config.embedding_dimensions,
                time_decay_half_life_hours=config.time_decay_half_life_hours,
            )
            embedder = LiteLLMEmbedder(
                model=config.embedding_model,
                dimensions=config.embedding_dimensions,
                cache_size=config.embedding_cache_size,
            )
        elif config.vector_backend == "qdrant":
            store = QdrantStore(
                url=config.qdrant_url,
                collection_name=config.qdrant_collection_name,
                embedding_dimensions=config.embedding_dimensions,
                time_decay_half_life_hours=config.time_decay_half_life_hours,
            )
            embedder = LiteLLMEmbedder(
                model=config.embedding_model,
                dimensions=config.embedding_dimensions,
                cache_size=config.embedding_cache_size,
            )
        else:
            raise ValueError(f"Unsupported vector_backend: {config.vector_backend}")
        return cls(store=store, embedder=embedder, config=config)
