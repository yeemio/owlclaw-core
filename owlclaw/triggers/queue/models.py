"""Queue trigger data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class RawMessage:
    """Raw message returned by a queue adapter."""

    message_id: str
    body: bytes
    headers: dict[str, str]
    timestamp: datetime
    metadata: dict[str, Any]


@dataclass(slots=True)
class MessageEnvelope:
    """Normalized message passed to queue trigger processing."""

    message_id: str
    payload: dict[str, Any] | str | bytes
    headers: dict[str, str]
    received_at: datetime
    source: str
    dedup_key: str | None = None
    event_name: str | None = None
    tenant_id: str | None = None

    @classmethod
    def from_raw_message(
        cls,
        raw: RawMessage,
        source: str,
        parser: Any | None = None,
    ) -> MessageEnvelope:
        """Build a normalized envelope from a raw queue message."""
        payload = parser.parse(raw.body) if parser is not None else raw.body
        received_at = raw.timestamp
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)

        return cls(
            message_id=raw.message_id,
            payload=payload,
            headers=dict(raw.headers),
            received_at=received_at,
            source=source,
            dedup_key=raw.headers.get("x-dedup-key"),
            event_name=raw.headers.get("x-event-name"),
            tenant_id=raw.headers.get("x-tenant-id"),
        )
