"""Signal trigger data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import UUID, uuid4


class SignalType(str, Enum):
    PAUSE = "pause"
    RESUME = "resume"
    TRIGGER = "trigger"
    INSTRUCT = "instruct"


class SignalSource(str, Enum):
    CLI = "cli"
    API = "api"
    MCP = "mcp"


@dataclass(slots=True)
class Signal:
    """One manual operation request from CLI/API/MCP."""

    type: SignalType
    source: SignalSource
    agent_id: str
    tenant_id: str
    operator: str
    message: str = ""
    focus: str | None = None
    ttl_seconds: int = 3600
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class SignalResult:
    """Signal operation result."""

    status: str
    message: str | None = None
    run_id: str | None = None
    error_code: str | None = None


@dataclass(slots=True)
class PendingInstruction:
    """Persistable operator instruction for next run injection."""

    content: str
    operator: str
    source: SignalSource
    created_at: datetime
    expires_at: datetime
    consumed: bool = False

    @classmethod
    def create(
        cls,
        *,
        content: str,
        operator: str,
        source: SignalSource,
        ttl_seconds: int,
    ) -> PendingInstruction:
        now = datetime.now(timezone.utc)
        return cls(
            content=content,
            operator=operator,
            source=source,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            consumed=False,
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        at = now if now is not None else datetime.now(timezone.utc)
        return at >= self.expires_at
