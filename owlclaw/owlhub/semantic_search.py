"""Semantic search utilities for OwlHub skill and template recommendation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from owlclaw.agent.memory.embedder_litellm import LiteLLMEmbedder


@dataclass(frozen=True)
class SemanticDocument:
    """One semantic document used for ranking."""

    doc_id: str
    text: str


class SemanticSearchCache:
    """File-based cache for skill/template embeddings."""

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path

    def load(self) -> dict[str, object]:
        if not self.cache_path.exists():
            return {}
        raw = self.cache_path.read_text(encoding="utf-8")
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}

    def save(self, payload: dict[str, object]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class SemanticSearcher:
    """Rank documents by cosine similarity of embeddings."""

    def __init__(
        self,
        *,
        cache_path: Path,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
    ) -> None:
        self._cache = SemanticSearchCache(cache_path)
        self._embedder = LiteLLMEmbedder(model=model, dimensions=dimensions, cache_size=0)
        self._model = model
        self._dimensions = dimensions

    def rank(self, query: str, documents: list[SemanticDocument], *, top_k: int = 10) -> list[tuple[str, float]]:
        return asyncio.run(self._rank_async(query=query, documents=documents, top_k=top_k))

    async def _rank_async(
        self,
        *,
        query: str,
        documents: list[SemanticDocument],
        top_k: int,
    ) -> list[tuple[str, float]]:
        normalized_query = query.strip()
        if not normalized_query or not documents:
            return []

        embeddings = await self._get_document_embeddings(documents)
        query_vector = await self._embedder.embed(normalized_query)

        scored: list[tuple[str, float]] = []
        for doc in documents:
            vector = embeddings.get(doc.doc_id)
            if vector is None:
                continue
            score = _cosine_similarity(query_vector, vector)
            scored.append((doc.doc_id, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[: max(1, top_k)]

    async def _get_document_embeddings(self, documents: list[SemanticDocument]) -> dict[str, list[float]]:
        payload = self._cache.load()
        content_hash = _documents_hash(documents)
        if (
            payload.get("model") == self._model
            and payload.get("dimensions") == self._dimensions
            and payload.get("content_hash") == content_hash
            and isinstance(payload.get("embeddings"), dict)
        ):
            cached = payload["embeddings"]
            if isinstance(cached, dict):
                normalized: dict[str, list[float]] = {}
                for key, value in cached.items():
                    if isinstance(key, str) and isinstance(value, list):
                        normalized[key] = [float(v) for v in value]
                if normalized:
                    return normalized

        texts = [doc.text for doc in documents]
        vectors = await self._embedder.embed_batch(texts)
        fresh = {doc.doc_id: vector for doc, vector in zip(documents, vectors, strict=True)}
        self._cache.save(
            {
                "model": self._model,
                "dimensions": self._dimensions,
                "content_hash": content_hash,
                "embeddings": fresh,
            }
        )
        return fresh


def _documents_hash(documents: list[SemanticDocument]) -> str:
    material = "\n".join(f"{doc.doc_id}:{doc.text}" for doc in sorted(documents, key=lambda item: item.doc_id))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)
