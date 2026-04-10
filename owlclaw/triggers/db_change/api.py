"""Public registration APIs for db_change triggers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from owlclaw.triggers.db_change.config import DBChangeTriggerConfig


@dataclass(slots=True)
class DBChangeTriggerRegistration:
    """Normalized registration payload for app.trigger(db_change(...))."""

    config: DBChangeTriggerConfig


def db_change(
    *,
    channel: str,
    event_name: str,
    agent_id: str,
    tenant_id: str = "default",
    debounce_seconds: float | None = None,
    batch_size: int | None = None,
    max_buffer_events: int = 1000,
    max_payload_bytes: int = 7900,
    focus: str | None = None,
    source: str = "notify",
    **_: Any,
) -> DBChangeTriggerRegistration:
    """Create a db-change trigger registration payload."""
    return DBChangeTriggerRegistration(
        config=DBChangeTriggerConfig(
            tenant_id=tenant_id,
            channel=channel,
            event_name=event_name,
            agent_id=agent_id,
            debounce_seconds=debounce_seconds,
            batch_size=batch_size,
            max_buffer_events=max_buffer_events,
            max_payload_bytes=max_payload_bytes,
            focus=focus,
            source=source,
        )
    )
