"""Queue adapter protocol shared by trigger implementations and adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from owlclaw.triggers.queue.models import RawMessage


class QueueAdapter(Protocol):
    """Abstract queue adapter interface."""

    async def connect(self) -> None:
        """Connect to backing queue system."""

    def consume(self) -> AsyncIterator[RawMessage]:
        """Yield queued messages."""

    async def ack(self, message: RawMessage) -> None:
        """Acknowledge successful message processing."""

    async def nack(self, message: RawMessage, requeue: bool = False) -> None:
        """Reject message processing."""

    async def send_to_dlq(self, message: RawMessage, reason: str) -> None:
        """Send failed message to dead-letter queue."""

    async def close(self) -> None:
        """Release adapter resources."""

    async def health_check(self) -> bool:
        """Return whether adapter connection is healthy."""
