"""Random EmbeddingProvider with fixed seed for reproducible tests (mock_mode)."""

from __future__ import annotations

import hashlib

from owlclaw.agent.memory.embedder import EmbeddingProvider


class RandomEmbedder(EmbeddingProvider):
    """Deterministic random vectors from text hash + seed. For mock_mode and tests."""

    def __init__(self, dimensions: int = 1536, seed: int = 42) -> None:
        self._dimensions = dimensions
        self._rng = __import__("random").Random(seed)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _vector_for(self, text: str) -> list[float]:
        """Deterministic vector from text (same text -> same vector)."""
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self._rng.seed(int(h[:16], 16))
        return [self._rng.random() for _ in range(self._dimensions)]

    async def embed(self, text: str) -> list[float]:
        return self._vector_for(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vector_for(t) for t in texts]
