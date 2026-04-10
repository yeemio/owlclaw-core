"""Embedding provider abstraction — EmbeddingProvider ABC and implementations (LiteLLM, TF-IDF, Random)."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Abstract base for generating text embeddings."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        ...

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Embedding vector dimension."""
        ...
