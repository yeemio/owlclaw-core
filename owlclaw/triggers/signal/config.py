"""Signal trigger configuration models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class SignalTriggerConfig(BaseModel):
    """Configuration for signal trigger subsystem."""

    default_instruct_ttl_seconds: int = Field(default=3600, ge=1, le=86400)
    max_pending_instructions: int = Field(default=10, ge=1, le=1000)
    require_auth_for_cli: bool = False
    require_auth_for_api: bool = True

    @field_validator("default_instruct_ttl_seconds", "max_pending_instructions")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be > 0")
        return value
