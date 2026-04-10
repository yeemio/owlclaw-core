"""Idempotency stores for queue trigger deduplication."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any


class IdempotencyStore(ABC):
    """Abstract idempotency store used by queue trigger."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return whether key already exists and not expired."""

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int) -> None:
        """Store value with TTL in seconds."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Get stored value when key exists and not expired."""


class RedisIdempotencyStore(IdempotencyStore):
    """Redis-backed idempotency store with key prefix support."""

    def __init__(self, client: Any, *, key_prefix: str = "idempotency:") -> None:
        self._client = client
        self._key_prefix = key_prefix

    def _key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    async def exists(self, key: str) -> bool:
        return bool(await self._client.exists(self._key(key)))

    async def set(self, key: str, value: Any, ttl: int) -> None:
        await self._client.set(self._key(key), json.dumps(value, ensure_ascii=False), ex=max(1, int(ttl)))

    async def get(self, key: str) -> Any | None:
        raw = await self._client.get(self._key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        return raw


class MockIdempotencyStore(IdempotencyStore):
    """In-memory idempotency store for tests and local runs."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[Any, float]] = {}

    def _purge_expired(self, key: str) -> None:
        item = self._items.get(key)
        if item is None:
            return
        _, expires_at = item
        if time.time() >= expires_at:
            self._items.pop(key, None)

    async def exists(self, key: str) -> bool:
        self._purge_expired(key)
        return key in self._items

    async def set(self, key: str, value: Any, ttl: int) -> None:
        self._items[key] = (value, time.time() + max(1, int(ttl)))

    async def get(self, key: str) -> Any | None:
        self._purge_expired(key)
        item = self._items.get(key)
        if item is None:
            return None
        return item[0]
