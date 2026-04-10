"""Configuration models for database change trigger."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DBChangeTriggerConfig(BaseModel):
    """Configuration for one db change trigger registration."""

    tenant_id: str = "default"
    channel: str
    event_name: str
    agent_id: str
    debounce_seconds: float | None = Field(default=None, ge=0)
    batch_size: int | None = Field(default=None, ge=1)
    max_buffer_events: int = Field(default=1000, ge=1)
    max_payload_bytes: int = Field(default=7900, ge=128, le=8000)
    focus: str | None = None
    source: str = "notify"

    @field_validator("tenant_id", "channel", "event_name", "agent_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be non-empty")
        return normalized


class DebeziumConfig(BaseModel):
    """CDC connector configuration for future Debezium-based adapter."""

    enabled: bool = False
    source_url: str
    connector_name: str
    topic_prefix: str = "dbserver1"
    startup_mode: Literal["latest", "earliest"] = "latest"

    @field_validator("source_url", "connector_name", "topic_prefix")
    @classmethod
    def _non_empty_cdc(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be non-empty")
        return normalized
