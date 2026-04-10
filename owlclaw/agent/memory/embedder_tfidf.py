"""TF-IDF embedding provider for memory degradation fallback."""

from __future__ import annotations

from typing import Any

from owlclaw.agent.memory.embedder import EmbeddingProvider


class TFIDFEmbedder(EmbeddingProvider):
    """Generate fixed-length vectors using scikit-learn TF-IDF."""

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be > 0")
        try:
            from sklearn.feature_extraction.text import HashingVectorizer  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - import error path is env-specific
            raise RuntimeError("TFIDFEmbedder requires scikit-learn. Install with `poetry add scikit-learn`.") from exc
        self._dimensions = dimensions
        # HashingVectorizer is stateless and works for online degradation fallback.
        self._vectorizer: Any = HashingVectorizer(
            n_features=dimensions,
            alternate_sign=False,
            norm="l2",
            lowercase=True,
        )

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        matrix = self._vectorizer.transform(texts)
        dense = matrix.toarray()
        return [[float(v) for v in row.tolist()] for row in dense]

    @property
    def dimensions(self) -> int:
        return self._dimensions
