"""Configuration models for API call trigger."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class APITriggerConfig(BaseModel):
    """Configuration for one API trigger endpoint."""

    path: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    event_name: str
    tenant_id: str = "default"
    response_mode: Literal["sync", "async"] = "async"
    sync_timeout_seconds: int = Field(default=60, ge=1, le=300)
    focus: str | None = None
    auth_required: bool = True
    description: str | None = None

    @field_validator("path", "event_name", "tenant_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be non-empty")
        return normalized

    @field_validator("path")
    @classmethod
    def _path_style(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("path must start with '/'")
        return value
