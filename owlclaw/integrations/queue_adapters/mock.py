"""In-memory queue adapter for local testing and CI."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator

from owlclaw.triggers.queue.models import RawMessage


class MockQueueAdapter:
    """Simple in-memory adapter implementing queue semantics for tests."""

    def __init__(self) -> None:
        self._queue: deque[RawMessage] = deque()
        self._acked: list[str] = []
        self._nacked: list[tuple[str, bool]] = []
        self._dlq: list[tuple[str, str]] = []
        self._published: list[dict[str, object]] = []
        self._connected = False
        self._closed = False

    async def connect(self) -> None:
        self._connected = True
        self._closed = False

    async def consume(self) -> AsyncIterator[RawMessage]:
        while self._connected and not self._closed:
            if self._queue:
                yield self._queue.popleft()
                continue
            await asyncio.sleep(0)
            break

    async def ack(self, message: RawMessage) -> None:
        self._acked.append(message.message_id)

    async def nack(self, message: RawMessage, requeue: bool = False) -> None:
        self._nacked.append((message.message_id, requeue))
        if requeue:
            self._queue.append(message)

    async def send_to_dlq(self, message: RawMessage, reason: str) -> None:
        self._dlq.append((message.message_id, reason))

    async def close(self) -> None:
        self._closed = True
        self._connected = False

    async def health_check(self) -> bool:
        return self._connected and not self._closed

    def enqueue(self, message: RawMessage) -> None:
        self._queue.append(message)

    def get_acked(self) -> list[str]:
        return list(self._acked)

    def get_nacked(self) -> list[tuple[str, bool]]:
        return list(self._nacked)

    def get_dlq(self) -> list[tuple[str, str]]:
        return list(self._dlq)

    async def publish(self, topic: str, message: bytes, headers: dict[str, str] | None = None) -> None:
        self._published.append(
            {
                "topic": topic,
                "message": message,
                "headers": dict(headers or {}),
            }
        )

    def get_published(self) -> list[dict[str, object]]:
        return list(self._published)

    def pending_count(self) -> int:
        return len(self._queue)
