"""AgentRunContext — single-run context passed through the entire Agent pipeline."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentRunContext:
    """Context for a single Agent run.

    Created once per invocation and threaded through identity loading,
    memory recall, skill selection, and the decision loop.

    Attributes:
        agent_id: Stable identifier for the Agent (usually the app name).
        run_id: UUID for this specific run; auto-generated when omitted.
        trigger: Source of this run — ``"cron"``, ``"schedule_once"``,
            ``"webhook"``, ``"heartbeat"``, ``"manual"``, etc.
        payload: Arbitrary JSON-serialisable context from the trigger source.
        focus: Optional tag from ``@app.cron(focus=...)`` or
            ``schedule_once``; narrows Skill loading to relevant subset.
        tenant_id: Multi-tenancy identifier; defaults to ``"default"``.
    """

    agent_id: str
    trigger: str
    payload: dict[str, Any] = field(default_factory=dict)
    focus: str | None = None
    tenant_id: str = "default"
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        for field_name in ("agent_id", "trigger", "tenant_id", "run_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            setattr(self, field_name, value.strip())
        if self.focus is not None:
            if not isinstance(self.focus, str):
                self.focus = None
            else:
                self.focus = self.focus.strip() or None
        if self.payload is None:
            self.payload = {}
        elif not isinstance(self.payload, dict):
            raise ValueError("payload must be a dictionary")
        else:
            self.payload = dict(self.payload)
