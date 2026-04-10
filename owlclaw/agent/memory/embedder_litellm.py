"""LiteLLM-backed EmbeddingProvider with cache, retry, and optional Langfuse span."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from owlclaw.agent.memory.embedder import EmbeddingProvider
from owlclaw.integrations import llm as llm_integration

logger = logging.getLogger(__name__)

# Batch size limit per LiteLLM call (task: ≤ 100)
_BATCH_SIZE = 100

# Retry: 3 attempts, exponential backoff base seconds
_MAX_RETRIES = 3
_BACKOFF_BASE = 1.0


def _cache_key(text: str) -> str:
    """Stable key for LRU cache (avoid storing very long strings as dict keys)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LiteLLMEmbedder(EmbeddingProvider):
    """Embedding via litellm.aembedding with LRU cache, retries, and optional Langfuse span."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        cache_size: int = 1000,
        timeout_seconds: float = 30.0,
        langfuse_span_callback: Callable[[str, int], AbstractContextManager[Any] | None] | None = None,
    ) -> None:
        """Create embedder.

        Args:
            model: LiteLLM embedding model id (e.g. text-embedding-3-small).
            dimensions: Output vector dimension (used for OpenAI 3.x models).
            cache_size: Max entries in LRU cache (0 = disable cache).
            langfuse_span_callback: Optional (name, input_count) -> span context manager for tracing.
        """
        self._model = model
        self._dimensions = dimensions
        self._cache_size = cache_size
        timeout = float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout_seconds = timeout
        self._langfuse_span_callback = langfuse_span_callback
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def _call_aembedding(self, input_texts: list[str]) -> list[list[float]]:
        """Call litellm.aembedding with retries and optional Langfuse span."""
        async def _do() -> list[list[float]]:
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    kwargs: dict[str, Any] = {
                        "model": self._model,
                        "input": input_texts,
                        "timeout": self._timeout_seconds,
                    }
                    if self._dimensions and "embedding-3" in self._model:
                        kwargs["dimensions"] = self._dimensions
                    response = await llm_integration.aembedding(**kwargs)
                    # response.data = [{"embedding": [...]}, ...]
                    if isinstance(response, dict):
                        data = response.get("data", [])
                    else:
                        data = getattr(response, "data", []) or []
                    out = []
                    for i, item in enumerate(data):
                        emb = item.get("embedding") if isinstance(item, dict) else getattr(item, "embedding", None)
                        if emb is None:
                            raise ValueError(f"Missing embedding at index {i}")
                        vector = list(emb)
                        if self._dimensions and len(vector) != self._dimensions:
                            raise ValueError(
                                f"Embedding dimensions mismatch at index {i}: "
                                f"expected {self._dimensions}, got {len(vector)}"
                            )
                        out.append(vector)
                    if len(out) != len(input_texts):
                        raise ValueError(
                            f"Embedding count mismatch: expected {len(input_texts)}, got {len(out)}"
                        )
                    return out
                except Exception as e:
                    if attempt == _MAX_RETRIES:
                        logger.exception("LiteLLM embedding failed after %s attempts: %s", _MAX_RETRIES, e)
                        raise
                    delay = _BACKOFF_BASE * (2.0 ** (attempt - 1))
                    logger.warning("LiteLLM embedding attempt %s failed, retry in %.1fs: %s", attempt, delay, e)
                    await asyncio.sleep(delay)
            return []
        if self._langfuse_span_callback:
            span_ctx = self._langfuse_span_callback("embedding", len(input_texts))
            if span_ctx is not None:
                with span_ctx:
                    return await _do()
        return await _do()

    async def embed(self, text: str) -> list[float]:
        key = _cache_key(text)
        if self._cache_size > 0 and key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key][:]
        vectors = await self._call_aembedding([text])
        if len(vectors) != 1:
            raise ValueError(f"Embedding count mismatch: expected 1, got {len(vectors)}")
        result = vectors[0]
        if self._dimensions and len(result) != self._dimensions:
            raise ValueError(
                f"Embedding dimensions mismatch: expected {self._dimensions}, got {len(result)}"
            )
        if self._cache_size > 0:
            if key in self._cache:
                self._cache.move_to_end(key)
            else:
                while len(self._cache) >= self._cache_size:
                    self._cache.popitem(last=False)
                self._cache[key] = result[:]
        return result

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Fill from cache and collect misses
        result: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []
        for i, t in enumerate(texts):
            key = _cache_key(t)
            if self._cache_size > 0 and key in self._cache:
                self._cache.move_to_end(key)
                result[i] = self._cache[key][:]
            else:
                miss_indices.append(i)
                miss_texts.append(t)
        # Batch call for misses (chunks of ≤ 100)
        for chunk_start in range(0, len(miss_texts), _BATCH_SIZE):
            chunk = miss_texts[chunk_start : chunk_start + _BATCH_SIZE]
            chunk_indices = miss_indices[chunk_start : chunk_start + _BATCH_SIZE]
            vectors = await self._call_aembedding(chunk)
            if len(vectors) != len(chunk):
                raise ValueError(
                    f"Embedding count mismatch: expected {len(chunk)}, got {len(vectors)}"
                )
            for idx, vec in zip(chunk_indices, vectors, strict=True):
                if self._dimensions and len(vec) != self._dimensions:
                    raise ValueError(
                        f"Embedding dimensions mismatch at index {idx}: "
                        f"expected {self._dimensions}, got {len(vec)}"
                    )
                result[idx] = vec
                if self._cache_size > 0:
                    key = _cache_key(texts[idx])
                    while len(self._cache) >= self._cache_size and key not in self._cache:
                        self._cache.popitem(last=False)
                    if key not in self._cache or len(self._cache) < self._cache_size:
                        self._cache[key] = vec[:]
                        self._cache.move_to_end(key)
        return [r for r in result if r is not None]
