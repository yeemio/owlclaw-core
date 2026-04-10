"""Signal-based agent manual control CLI commands."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from owlclaw.db import create_session_factory, get_engine
from owlclaw.triggers.signal import AgentStateManager, Signal, SignalRouter, SignalSource, SignalType, default_handlers

logger = logging.getLogger(__name__)


@dataclass
class _Runtime:
    async def trigger_event(
        self,
        event_name: str,
        payload: dict[str, Any],
        focus: str | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        return {
            "event_name": event_name,
            "payload": payload,
            "focus": focus,
            "tenant_id": tenant_id,
            "run_id": f"run-{uuid.uuid4().hex}",
        }


class _Governance:
    async def allow_trigger(self, event_name: str, tenant_id: str) -> bool:  # noqa: ARG002
        return True


def _build_state_manager() -> AgentStateManager:
    """Prefer DB-backed state for CLI process boundaries; fallback to in-memory."""
    try:
        session_factory = create_session_factory(get_engine())
        return AgentStateManager(max_pending_instructions=20, session_factory=session_factory)
    except Exception as exc:
        logger.debug("Signal CLI fallback to in-memory state manager: %s", exc)
        return AgentStateManager(max_pending_instructions=20)


_state_manager = _build_state_manager()
_router = SignalRouter(
    handlers=default_handlers(
        state=_state_manager,
        runtime=_Runtime(),
        governance=_Governance(),
    )
)


def _dispatch(signal: Signal) -> dict[str, Any]:
    result = asyncio.run(_router.dispatch(signal))
    return {
        "status": result.status,
        "message": result.message,
        "run_id": result.run_id,
        "error_code": result.error_code,
    }


def pause_command(*, agent: str, tenant: str, operator: str) -> dict[str, Any]:
    signal = Signal(type=SignalType.PAUSE, source=SignalSource.CLI, agent_id=agent, tenant_id=tenant, operator=operator)
    return _dispatch(signal)


def resume_command(*, agent: str, tenant: str, operator: str) -> dict[str, Any]:
    signal = Signal(type=SignalType.RESUME, source=SignalSource.CLI, agent_id=agent, tenant_id=tenant, operator=operator)
    return _dispatch(signal)


def trigger_command(*, agent: str, tenant: str, operator: str, message: str, focus: str | None) -> dict[str, Any]:
    signal = Signal(
        type=SignalType.TRIGGER,
        source=SignalSource.CLI,
        agent_id=agent,
        tenant_id=tenant,
        operator=operator,
        message=message,
        focus=focus,
    )
    return _dispatch(signal)


def instruct_command(*, agent: str, tenant: str, operator: str, message: str, ttl_seconds: int) -> dict[str, Any]:
    signal = Signal(
        type=SignalType.INSTRUCT,
        source=SignalSource.CLI,
        agent_id=agent,
        tenant_id=tenant,
        operator=operator,
        message=message,
        ttl_seconds=ttl_seconds,
    )
    return _dispatch(signal)


def status_command(*, agent: str, tenant: str) -> dict[str, Any]:
    state = asyncio.run(_state_manager.get(agent, tenant))
    return {
        "agent_id": agent,
        "tenant_id": tenant,
        "paused": state.paused,
        "pending_instructions": len(state.pending_instructions),
    }
