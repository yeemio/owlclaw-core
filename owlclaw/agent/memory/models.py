"""Data models for Agent Memory — MemoryEntry, SecurityLevel, MemoryConfig."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class SecurityLevel(str, Enum):
    """Security classification for a memory entry."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


@dataclass
class MemoryEntry:
    """Single long-term memory entry (STM/LTM layer)."""

    id: UUID = field(default_factory=uuid4)
    agent_id: str = ""
    tenant_id: str = ""
    content: str = ""
    embedding: list[float] | None = None
    tags: list[str] = field(default_factory=list)
    security_level: SecurityLevel = SecurityLevel.INTERNAL
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    accessed_at: datetime | None = None
    access_count: int = 0
    archived: bool = False


@dataclass
class MemorySnapshot:
    """Preloaded LTM snapshot for a Run: prompt fragment + source entry ids."""

    prompt_fragment: str = ""
    entry_ids: list[UUID] = field(default_factory=list)


@dataclass
class RecallResult:
    """Single result from recall(): entry and similarity score."""

    entry: MemoryEntry = field(default_factory=MemoryEntry)
    score: float = 0.0


@dataclass
class CompactionResult:
    """Result summary of one memory compaction run."""

    merged_groups: int = 0
    archived_entries: int = 0
    created_summaries: int = 0


class MemoryConfig(BaseModel):
    """Pydantic model for owlclaw.yaml memory section."""

    vector_backend: str = "pgvector"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, gt=0)
    stm_max_tokens: int = Field(default=2000, gt=0)
    snapshot_max_tokens: int = Field(default=500, gt=0)
    snapshot_semantic_limit: int = Field(default=3, gt=0)
    snapshot_recent_hours: int = Field(default=24, ge=0)
    snapshot_recent_limit: int = Field(default=5, gt=0)
    time_decay_half_life_hours: float = Field(default=168.0, gt=0)
    max_entries: int = Field(default=10000, gt=0)
    retention_days: int = Field(default=365, gt=0)
    compaction_threshold: int = Field(default=50, gt=0)
    embedding_cache_size: int = Field(default=1000, ge=0)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_name: str = "owlclaw_memory"
    tfidf_dimensions: int = Field(default=256, gt=0)
    enable_tfidf_fallback: bool = True
    enable_keyword_fallback: bool = True
    enable_file_fallback: bool = True
    file_fallback_path: str = "MEMORY.md"

    model_config = ConfigDict(extra="ignore")
