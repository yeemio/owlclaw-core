"""MemorySystem — short-term memory manager for current Agent run."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class _ShortTermEntry:
    role: str
    content: str


@dataclass
class _LongTermEntry:
    content: str
    tags: list[str]
    embedding: list[float]
    created_at: datetime


logger = logging.getLogger(__name__)


class MemorySystem:
    """Manage short-term run context with token budget and auto-compression."""

    def __init__(
        self,
        short_term_token_limit: int = 2000,
        *,
        memory_file: str | None = None,
        memory_file_size_limit_bytes: int = 10 * 1024 * 1024,
        vector_index: Any | None = None,
        embedder: Any | None = None,
    ) -> None:
        if not isinstance(short_term_token_limit, int) or short_term_token_limit < 1:
            raise ValueError("short_term_token_limit must be a positive integer")
        if not isinstance(memory_file_size_limit_bytes, int) or memory_file_size_limit_bytes < 1024:
            raise ValueError("memory_file_size_limit_bytes must be an integer >= 1024")
        self.short_term_token_limit = short_term_token_limit
        self.memory_file_size_limit_bytes = memory_file_size_limit_bytes
        self.memory_file = Path(memory_file) if memory_file else None
        self.vector_index = vector_index
        self.embedder = embedder
        self.vector_index_degraded = False
        self._short_term_entries: list[_ShortTermEntry] = []
        self._long_term_entries: list[_LongTermEntry] = []

    def add_short_term(self, role: str, content: str) -> None:
        """Append a short-term memory entry for this run."""
        normalized_role = (role or "").strip()
        normalized_content = (content or "").strip()
        if not normalized_role:
            raise ValueError("role must be a non-empty string")
        if not normalized_content:
            raise ValueError("content must be a non-empty string")
        self._short_term_entries.append(
            _ShortTermEntry(role=normalized_role, content=normalized_content)
        )

    def build_short_term_context(self) -> str:
        """Build compressed short-term context constrained by token limit."""
        if not self._short_term_entries:
            return ""

        lines = [f"{entry.role}: {entry.content}" for entry in self._short_term_entries]
        compressed_lines, removed = self._compress_to_limit(lines)
        if removed > 0:
            compressed_lines.insert(0, f"[compressed {removed} earlier entries]")
        return "\n".join(compressed_lines).strip()

    def write(self, content: str, tags: list[str] | None = None) -> dict[str, Any]:
        """Persist a long-term memory record to MEMORY.md and vector index."""
        normalized_content = (content or "").strip()
        if not normalized_content:
            raise ValueError("content must be a non-empty string")
        normalized_tags = self._normalize_tags(tags)
        created_at = datetime.now(timezone.utc)
        embedding = self._embed(normalized_content)
        entry = _LongTermEntry(
            content=normalized_content,
            tags=normalized_tags,
            embedding=embedding,
            created_at=created_at,
        )
        self._long_term_entries.append(entry)
        self._append_to_memory_file(entry)
        self._enforce_memory_file_limit()
        self._index_entry(entry)
        return {
            "content": entry.content,
            "tags": entry.tags,
            "created_at": entry.created_at.isoformat(),
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search long-term memory by embedding similarity with time decay."""
        normalized_query = (query or "").strip()
        if not normalized_query:
            return []
        effective_limit = max(1, int(limit))
        tag_filter = set(self._normalize_tags(tags))
        query_embedding = self._embed(normalized_query)
        query_tokens = {token for token in normalized_query.lower().split() if token}
        now = datetime.now(timezone.utc)
        scored: list[tuple[float, _LongTermEntry]] = []
        for entry in self._long_term_entries:
            if tag_filter and not (set(entry.tags) & tag_filter):
                continue
            similarity = self._cosine_similarity(query_embedding, entry.embedding)
            entry_tokens = {token for token in entry.content.lower().split() if token}
            lexical = (len(query_tokens & entry_tokens) / len(query_tokens)) if query_tokens else 0.0
            age_days = max(0.0, (now - entry.created_at).total_seconds() / 86400.0)
            decay = math.exp(-age_days / 30.0)
            score = (lexical + (0.2 * similarity)) * decay
            scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        out: list[dict[str, Any]] = []
        for score, entry in scored[:effective_limit]:
            out.append(
                {
                    "content": entry.content,
                    "tags": list(entry.tags),
                    "score": score,
                    "created_at": entry.created_at.isoformat(),
                }
            )
        return out

    def recall_relevant(
        self,
        query: str,
        *,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Return only content strings for relevant memory hits."""
        return [item["content"] for item in self.search(query, limit=limit, tags=tags)]

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate tokens with a lightweight heuristic suitable for tests/runtime guardrails."""
        if not text or not text.strip():
            return 0
        return len(text.split())

    def _compress_to_limit(self, lines: list[str]) -> tuple[list[str], int]:
        """Keep newest entries within token budget; return (kept_lines, removed_count)."""
        if self.estimate_tokens("\n".join(lines)) <= self.short_term_token_limit:
            return lines, 0

        kept: list[str] = []
        total_tokens = 0
        for line in reversed(lines):
            line_tokens = self.estimate_tokens(line)
            if total_tokens + line_tokens > self.short_term_token_limit:
                continue
            kept.append(line)
            total_tokens += line_tokens
        kept.reverse()

        if not kept:
            # Extremely small limit: keep tail line truncated to fit.
            tail = lines[-1]
            words = tail.split()
            kept = [" ".join(words[: self.short_term_token_limit])]
        removed = max(0, len(lines) - len(kept))
        return kept, removed

    @staticmethod
    def _normalize_tags(tags: list[str] | None) -> list[str]:
        if not tags:
            return []
        normalized: list[str] = []
        for tag in tags:
            value = str(tag).strip().lower()
            if value and value not in normalized:
                normalized.append(value)
        return normalized

    def _embed(self, text: str) -> list[float]:
        if self.embedder is not None:
            vector = self.embedder(text)
            if isinstance(vector, list) and vector:
                return [float(v) for v in vector]
        words = text.lower().split()
        if not words:
            return [0.0, 0.0, 0.0, 0.0]
        vector = [0.0, 0.0, 0.0, 0.0]
        for idx, word in enumerate(words):
            vector[idx % 4] += float(sum(ord(ch) for ch in word) % 1000)
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _append_to_memory_file(self, entry: _LongTermEntry) -> None:
        if self.memory_file is None:
            return
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"- {entry.created_at.isoformat()} "
            f"[{','.join(entry.tags)}] {entry.content}\n"
        )
        with self.memory_file.open("a", encoding="utf-8") as f:
            f.write(line)

    def _enforce_memory_file_limit(self) -> None:
        if self.memory_file is None or not self.memory_file.exists():
            return
        size = self.memory_file.stat().st_size
        if size <= self.memory_file_size_limit_bytes:
            return
        archive_name = (
            f"{self.memory_file.stem}.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.archive.md"
        )
        archive_path = self.memory_file.with_name(archive_name)
        self.memory_file.replace(archive_path)
        self.memory_file.write_text("# MEMORY (rotated)\n", encoding="utf-8")

    def _index_entry(self, entry: _LongTermEntry) -> None:
        if self.vector_index is None:
            return
        payload = {
            "content": entry.content,
            "tags": entry.tags,
            "created_at": entry.created_at.isoformat(),
            "embedding": entry.embedding,
        }
        try:
            if hasattr(self.vector_index, "upsert"):
                self.vector_index.upsert(payload)
            elif hasattr(self.vector_index, "add"):
                self.vector_index.add(payload)
        except Exception as exc:
            self.vector_index_degraded = True
            logger.warning("Vector index degraded, falling back to MEMORY.md only: %s", exc)
