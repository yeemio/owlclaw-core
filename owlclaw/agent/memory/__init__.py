"""Agent Memory — STM + LTM, vector store, embedding, snapshot."""

from typing import Any

from owlclaw.agent.memory.decay import time_decay
from owlclaw.agent.memory.embedder import EmbeddingProvider
from owlclaw.agent.memory.embedder_litellm import LiteLLMEmbedder
from owlclaw.agent.memory.embedder_random import RandomEmbedder
from owlclaw.agent.memory.embedder_tfidf import TFIDFEmbedder
from owlclaw.agent.memory.lifecycle import MaintenanceResult, MemoryLifecycleManager
from owlclaw.agent.memory.migration import MemoryMigrationResult, migrate_store_data
from owlclaw.agent.memory.models import (
    CompactionResult,
    MemoryConfig,
    MemoryEntry,
    MemorySnapshot,
    RecallResult,
    SecurityLevel,
)
from owlclaw.agent.memory.service import MemoryService
from owlclaw.agent.memory.stm import STMManager
from owlclaw.agent.memory.store import MemoryStore
from owlclaw.agent.memory.store_inmemory import InMemoryStore
from owlclaw.agent.memory.store_qdrant import QdrantStore

try:
    from owlclaw.agent.memory.store_pgvector import PgVectorStore
except ModuleNotFoundError as exc:
    missing_name = exc.name or ""
    missing_text = str(exc)
    if missing_name.startswith("pgvector") or "pgvector" in missing_text:
        class PgVectorStore:  # type: ignore[no-redef]
            """Compatibility shim when pgvector optional dependency is missing."""

            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                raise ModuleNotFoundError(
                    "pgvector is required for PgVectorStore. "
                    "Install with `poetry add pgvector`."
                )
    else:
        raise

__all__ = [
    "EmbeddingProvider",
    "CompactionResult",
    "InMemoryStore",
    "MaintenanceResult",
    "MemoryMigrationResult",
    "MemoryLifecycleManager",
    "LiteLLMEmbedder",
    "MemoryConfig",
    "MemoryEntry",
    "MemoryService",
    "MemorySnapshot",
    "MemoryStore",
    "PgVectorStore",
    "QdrantStore",
    "RandomEmbedder",
    "RecallResult",
    "SecurityLevel",
    "STMManager",
    "TFIDFEmbedder",
    "migrate_store_data",
    "time_decay",
]
